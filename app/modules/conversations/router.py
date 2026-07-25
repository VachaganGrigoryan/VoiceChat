from __future__ import annotations

from typing import Annotated, Literal, Optional

import socketio
from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from starlette.requests import Request

from app.core.deps import get_sio
from app.core.errors.openapi import build_error_responses
from app.core.http import (
    PaginatedResponse,
    PaginationMeta,
    SuccessResponse,
    ok,
    ok_paginated,
)
from app.core.rate_limit import rate_limit
from app.core.security import require_verified_user
from app.modules.conversations.dependencies import get_conversations_service
from app.modules.conversations.repository.mappers import (
    to_invite_link_view,
    to_join_request_view,
    to_participant_view,
)
from app.modules.conversations.schemas import (
    AddGroupMembersRequest,
    BulkInboxStateRequest,
    BulkInboxStateResult,
    ConversationSendTextRequest,
    ConversationView,
    CreateChannelRequest,
    CreateGroupRequest,
    CreateDmRequest,
    CreateInviteRequest,
    FolderView,
    InviteLinkView,
    JoinRequestView,
    ParticipantView,
    RedeemInviteResponse,
    RenameFolderRequest,
    RenameFolderResult,
    TransferOwnershipRequest,
    UpdateConversationSettingsRequest,
    UpdateGroupRequest,
    UpdateInboxStateRequest,
    UpdateParticipantPermissionsRequest,
    UpdateParticipantRoleRequest,
)
from app.modules.conversations.service import ConversationsService
from app.modules.messages.dependencies import get_messages_service
from app.modules.messages.emit_helpers import emit_send_result
from app.modules.messages.schemas import (
    AddReactionRequest,
    ClearChatResponse,
    DeleteChatResponse,
    DeleteMessageResponse,
    EditMessageRequest,
    ForwardMessageRequest,
    MessageDoc,
    ScheduleMessageRequest,
    SendRichContentRequest,
    SetDraftRequest,
    ThreadSummary,
)
from app.modules.messages.service import MessagesService
from app.modules.notifications.dependencies import get_notifications_service
from app.modules.notifications.service import NotificationsService
from app.modules.realtime import emit_to_user

router = APIRouter(
    prefix="/conversations",
    tags=["conversations"],
    responses=build_error_responses(400, 401, 422, 500),
)


async def emit_message_notifications(
    sio: socketio.AsyncServer,
    *,
    notifications: NotificationsService,
    message: MessageDoc,
) -> None:
    generated = await notifications.generate_for_message(message=message)
    for item in generated:
        await emit_to_user(
            sio,
            item.notification.user_id,
            "notification_created",
            item.notification.model_dump(mode="json"),
        )


@router.post(
    "",
    status_code=200,
    response_model=SuccessResponse[ConversationView],
    dependencies=[Depends(rate_limit("30/minute", scope="conversation_create"))],
)
async def create_or_get_dm(
    request: Request,
    body: CreateDmRequest,
    user=Depends(require_verified_user),
    service: ConversationsService = Depends(get_conversations_service),
):
    conversation = await service.ensure_dm_conversation(
        user_id=user.str_id, peer_user_id=body.peer_user_id
    )
    data = (
        await service.views_for_user(user_id=user.str_id, conversations=[conversation])
    )[0]
    return ok(request, data=data)


@router.post(
    "/groups",
    status_code=201,
    response_model=SuccessResponse[ConversationView],
    dependencies=[Depends(rate_limit("20/minute", scope="group_conversation_create"))],
)
async def create_group(
    request: Request,
    body: CreateGroupRequest,
    user=Depends(require_verified_user),
    service: ConversationsService = Depends(get_conversations_service),
):
    conversation = await service.create_group_conversation(
        user_id=user.str_id,
        title=body.title,
        participant_ids=body.participant_ids,
        space_id=body.space_id,
        space_visibility=body.space_visibility,
    )
    data = (
        await service.views_for_user(user_id=user.str_id, conversations=[conversation])
    )[0]
    return ok(request, data=data, status_code=201)


@router.post(
    "/channels",
    status_code=201,
    response_model=SuccessResponse[ConversationView],
    responses=build_error_responses(409),
    dependencies=[
        Depends(rate_limit("20/minute", scope="channel_conversation_create"))
    ],
)
async def create_channel(
    request: Request,
    body: CreateChannelRequest,
    user=Depends(require_verified_user),
    service: ConversationsService = Depends(get_conversations_service),
):
    conversation = await service.create_channel_conversation(
        user_id=user.str_id,
        title=body.title,
        participant_ids=body.participant_ids,
        description=body.description,
        visibility=body.visibility,
        posting_policy=body.posting_policy,
        read_policy=body.read_policy,
        slug=body.slug,
        space_id=body.space_id,
        space_visibility=body.space_visibility,
    )
    data = (
        await service.views_for_user(user_id=user.str_id, conversations=[conversation])
    )[0]
    return ok(request, data=data, status_code=201)


@router.get(
    "/public/{slug}",
    response_model=SuccessResponse[ConversationView],
    responses=build_error_responses(404),
    dependencies=[Depends(rate_limit("30/minute", scope="conversation_public_lookup"))],
)
async def get_public_conversation(
    request: Request,
    slug: str,
    user=Depends(require_verified_user),
    service: ConversationsService = Depends(get_conversations_service),
):
    conversation = await service.get_public_conversation_by_slug(slug=slug)
    data = (
        await service.views_for_user(user_id=user.str_id, conversations=[conversation])
    )[0]
    return ok(request, data=data)


@router.post(
    "/invites/{code}/redeem",
    response_model=SuccessResponse[RedeemInviteResponse],
    responses=build_error_responses(404, 410),
    dependencies=[Depends(rate_limit("20/minute", scope="conversation_invite_redeem"))],
)
async def redeem_invite(
    request: Request,
    code: str,
    user=Depends(require_verified_user),
    service: ConversationsService = Depends(get_conversations_service),
):
    status, conversation, join_request = await service.redeem_invite(
        user_id=user.str_id, code=code
    )
    conversation_view = (
        (
            await service.views_for_user(
                user_id=user.str_id, conversations=[conversation]
            )
        )[0]
        if conversation is not None
        else None
    )
    return ok(
        request,
        data=RedeemInviteResponse(
            status=status,
            conversation=conversation_view,
            join_request=(
                to_join_request_view(join_request) if join_request is not None else None
            ),
        ),
    )


@router.post(
    "/{conversation_id}/invites",
    status_code=201,
    response_model=SuccessResponse[InviteLinkView],
    dependencies=[Depends(rate_limit("20/minute", scope="conversation_invite_create"))],
)
async def create_invite(
    request: Request,
    conversation_id: str,
    body: CreateInviteRequest,
    user=Depends(require_verified_user),
    service: ConversationsService = Depends(get_conversations_service),
):
    invite = await service.create_invite(
        actor_user_id=user.str_id,
        conversation_id=conversation_id,
        expires_at=body.expires_at,
        max_uses=body.max_uses,
        requires_approval=body.requires_approval,
    )
    return ok(request, data=to_invite_link_view(invite), status_code=201)


@router.get(
    "/{conversation_id}/invites",
    response_model=SuccessResponse[list[InviteLinkView]],
    dependencies=[Depends(rate_limit("30/minute", scope="conversation_invite_list"))],
)
async def list_invites(
    request: Request,
    conversation_id: str,
    user=Depends(require_verified_user),
    service: ConversationsService = Depends(get_conversations_service),
):
    invites = await service.list_invites(
        actor_user_id=user.str_id, conversation_id=conversation_id
    )
    return ok(request, data=[to_invite_link_view(item) for item in invites])


@router.delete(
    "/{conversation_id}/invites/{invite_id}",
    status_code=204,
    dependencies=[Depends(rate_limit("20/minute", scope="conversation_invite_revoke"))],
)
async def revoke_invite(
    request: Request,
    conversation_id: str,
    invite_id: str,
    user=Depends(require_verified_user),
    service: ConversationsService = Depends(get_conversations_service),
):
    await service.revoke_invite(
        actor_user_id=user.str_id,
        conversation_id=conversation_id,
        invite_id=invite_id,
    )
    return ok(request, data=None, status_code=204)


@router.get(
    "/{conversation_id}/join-requests",
    response_model=SuccessResponse[list[JoinRequestView]],
    dependencies=[Depends(rate_limit("30/minute", scope="conversation_join_requests"))],
)
async def list_join_requests(
    request: Request,
    conversation_id: str,
    user=Depends(require_verified_user),
    service: ConversationsService = Depends(get_conversations_service),
):
    requests = await service.list_join_requests(
        actor_user_id=user.str_id, conversation_id=conversation_id
    )
    return ok(request, data=[to_join_request_view(item) for item in requests])


@router.post(
    "/{conversation_id}/join-requests/{request_id}/approve",
    response_model=SuccessResponse[JoinRequestView],
    dependencies=[Depends(rate_limit("30/minute", scope="conversation_join_approve"))],
)
async def approve_join_request(
    request: Request,
    conversation_id: str,
    request_id: str,
    user=Depends(require_verified_user),
    service: ConversationsService = Depends(get_conversations_service),
):
    updated = await service.approve_join_request(
        actor_user_id=user.str_id,
        conversation_id=conversation_id,
        request_id=request_id,
    )
    return ok(request, data=to_join_request_view(updated))


@router.post(
    "/{conversation_id}/join-requests/{request_id}/reject",
    response_model=SuccessResponse[JoinRequestView],
    dependencies=[Depends(rate_limit("30/minute", scope="conversation_join_reject"))],
)
async def reject_join_request(
    request: Request,
    conversation_id: str,
    request_id: str,
    user=Depends(require_verified_user),
    service: ConversationsService = Depends(get_conversations_service),
):
    updated = await service.reject_join_request(
        actor_user_id=user.str_id,
        conversation_id=conversation_id,
        request_id=request_id,
    )
    return ok(request, data=to_join_request_view(updated))


@router.get(
    "",
    response_model=PaginatedResponse[list[ConversationView]],
    dependencies=[Depends(rate_limit("30/minute", scope="conversations_list"))],
)
async def list_conversations(
    request: Request,
    limit: int = Query(50, ge=1, le=100),
    cursor: Optional[str] = Query(None),
    archived: bool = Query(False),
    folder: Optional[str] = Query(None),
    space_id: Optional[str] = Query(None),
    user=Depends(require_verified_user),
    service: ConversationsService = Depends(get_conversations_service),
):
    items, next_cursor = await service.list_for_user(
        user_id=user.str_id,
        limit=limit,
        cursor=cursor,
        archived=archived,
        folder=folder,
        space_id=space_id,
    )
    data = await service.views_for_user(user_id=user.str_id, conversations=items)
    return ok_paginated(
        request,
        data=data,
        meta=PaginationMeta(cursor=cursor, next_cursor=next_cursor, limit=limit),
    )


# NOTE: the literal `/folders` and `/inbox` routes below must stay registered
# ahead of the dynamic `GET /{conversation_id}` route so they are not captured as
# a conversation id.
@router.get(
    "/folders",
    response_model=SuccessResponse[list[FolderView]],
    dependencies=[Depends(rate_limit("30/minute", scope="conversation_folders"))],
)
async def list_folders(
    request: Request,
    user=Depends(require_verified_user),
    service: ConversationsService = Depends(get_conversations_service),
):
    folders = await service.list_folders(user_id=user.str_id)
    return ok(request, data=[FolderView(**folder) for folder in folders])


@router.patch(
    "/folders/{name}",
    response_model=SuccessResponse[RenameFolderResult],
    dependencies=[Depends(rate_limit("30/minute", scope="conversation_folders"))],
)
async def rename_folder(
    request: Request,
    name: str,
    body: RenameFolderRequest,
    user=Depends(require_verified_user),
    service: ConversationsService = Depends(get_conversations_service),
):
    updated = await service.rename_folder(
        user_id=user.str_id, old_name=name, new_name=body.new_name
    )
    return ok(request, data=RenameFolderResult(updated=updated))


@router.delete(
    "/folders/{name}",
    status_code=204,
    dependencies=[Depends(rate_limit("30/minute", scope="conversation_folders"))],
)
async def delete_folder(
    request: Request,
    name: str,
    user=Depends(require_verified_user),
    service: ConversationsService = Depends(get_conversations_service),
):
    await service.delete_folder(user_id=user.str_id, name=name)
    return ok(request, data=None, status_code=204)


@router.patch(
    "/inbox",
    response_model=SuccessResponse[BulkInboxStateResult],
    dependencies=[Depends(rate_limit("30/minute", scope="conversation_inbox_bulk"))],
)
async def update_inbox_state_bulk(
    request: Request,
    body: BulkInboxStateRequest,
    user=Depends(require_verified_user),
    service: ConversationsService = Depends(get_conversations_service),
):
    updated = await service.set_inbox_state_bulk(
        user_id=user.str_id,
        conversation_ids=body.conversation_ids,
        updates=body.model_dump(exclude_unset=True, exclude={"conversation_ids"}),
    )
    return ok(request, data=BulkInboxStateResult(updated=updated))


@router.post(
    "/{conversation_id}/read",
    status_code=204,
    dependencies=[Depends(rate_limit("60/minute", scope="conversation_read"))],
)
async def mark_read(
    request: Request,
    conversation_id: str,
    user=Depends(require_verified_user),
    service: ConversationsService = Depends(get_conversations_service),
):
    await service.mark_conversation_read(
        user_id=user.str_id, conversation_id=conversation_id
    )
    return ok(request, data=None, status_code=204)


@router.patch(
    "/{conversation_id}/inbox",
    response_model=SuccessResponse[ParticipantView],
    dependencies=[Depends(rate_limit("60/minute", scope="conversation_inbox_state"))],
)
async def update_inbox_state(
    request: Request,
    conversation_id: str,
    body: UpdateInboxStateRequest,
    user=Depends(require_verified_user),
    service: ConversationsService = Depends(get_conversations_service),
):
    participant = await service.set_inbox_state(
        user_id=user.str_id,
        conversation_id=conversation_id,
        updates=body.model_dump(exclude_unset=True),
    )
    return ok(request, data=to_participant_view(participant))


@router.get(
    "/{conversation_id}",
    response_model=SuccessResponse[ConversationView],
    responses=build_error_responses(404),
    dependencies=[Depends(rate_limit("60/minute", scope="conversation_get"))],
)
async def get_conversation(
    request: Request,
    conversation_id: str,
    user=Depends(require_verified_user),
    service: ConversationsService = Depends(get_conversations_service),
):
    view = await service.get_conversation_view(
        user_id=user.str_id, conversation_id=conversation_id
    )
    return ok(request, data=view)


@router.get(
    "/{conversation_id}/members",
    response_model=SuccessResponse[list[ParticipantView]],
    dependencies=[Depends(rate_limit("30/minute", scope="conversation_members"))],
)
async def list_members(
    request: Request,
    conversation_id: str,
    user=Depends(require_verified_user),
    service: ConversationsService = Depends(get_conversations_service),
):
    participants = await service.list_group_participants(
        user_id=user.str_id, conversation_id=conversation_id
    )
    return ok(request, data=[to_participant_view(item) for item in participants])


@router.post(
    "/{conversation_id}/members",
    status_code=201,
    response_model=SuccessResponse[list[ParticipantView]],
    dependencies=[Depends(rate_limit("20/minute", scope="conversation_members_add"))],
)
async def add_members(
    request: Request,
    conversation_id: str,
    body: AddGroupMembersRequest,
    user=Depends(require_verified_user),
    service: ConversationsService = Depends(get_conversations_service),
):
    participants = await service.add_group_members(
        actor_user_id=user.str_id,
        conversation_id=conversation_id,
        participant_ids=body.participant_ids,
    )
    return ok(
        request,
        data=[to_participant_view(item) for item in participants],
        status_code=201,
    )


@router.patch(
    "/{conversation_id}/members/{member_user_id}/role",
    response_model=SuccessResponse[ParticipantView],
    dependencies=[Depends(rate_limit("20/minute", scope="conversation_members_role"))],
)
async def update_member_role(
    request: Request,
    conversation_id: str,
    member_user_id: str,
    body: UpdateParticipantRoleRequest,
    user=Depends(require_verified_user),
    service: ConversationsService = Depends(get_conversations_service),
):
    participant = await service.update_group_member_role(
        actor_user_id=user.str_id,
        conversation_id=conversation_id,
        target_user_id=member_user_id,
        role=body.role,
    )
    return ok(request, data=to_participant_view(participant))


@router.patch(
    "/{conversation_id}/members/{member_user_id}/permissions",
    response_model=SuccessResponse[ParticipantView],
    responses=build_error_responses(403, 404),
    dependencies=[
        Depends(rate_limit("20/minute", scope="conversation_members_permissions"))
    ],
)
async def update_member_permissions(
    request: Request,
    conversation_id: str,
    member_user_id: str,
    body: UpdateParticipantPermissionsRequest,
    user=Depends(require_verified_user),
    service: ConversationsService = Depends(get_conversations_service),
):
    participant = await service.set_member_permissions(
        actor_user_id=user.str_id,
        conversation_id=conversation_id,
        target_user_id=member_user_id,
        permissions=body.permissions,
    )
    return ok(request, data=to_participant_view(participant))


@router.post(
    "/{conversation_id}/ownership",
    response_model=SuccessResponse[list[ParticipantView]],
    dependencies=[Depends(rate_limit("10/minute", scope="conversation_ownership"))],
)
async def transfer_ownership(
    request: Request,
    conversation_id: str,
    body: TransferOwnershipRequest,
    user=Depends(require_verified_user),
    service: ConversationsService = Depends(get_conversations_service),
):
    participants = await service.transfer_group_ownership(
        actor_user_id=user.str_id,
        conversation_id=conversation_id,
        target_user_id=body.user_id,
    )
    return ok(request, data=[to_participant_view(item) for item in participants])


@router.delete(
    "/{conversation_id}/members/{member_user_id}",
    status_code=204,
    dependencies=[
        Depends(rate_limit("20/minute", scope="conversation_members_remove"))
    ],
)
async def remove_member(
    request: Request,
    conversation_id: str,
    member_user_id: str,
    user=Depends(require_verified_user),
    service: ConversationsService = Depends(get_conversations_service),
):
    await service.remove_group_member(
        actor_user_id=user.str_id,
        conversation_id=conversation_id,
        target_user_id=member_user_id,
    )
    return ok(request, data=None, status_code=204)


@router.post(
    "/{conversation_id}/leave",
    status_code=204,
    dependencies=[Depends(rate_limit("20/minute", scope="conversation_leave"))],
)
async def leave_group(
    request: Request,
    conversation_id: str,
    user=Depends(require_verified_user),
    service: ConversationsService = Depends(get_conversations_service),
):
    await service.leave_group(user_id=user.str_id, conversation_id=conversation_id)
    return ok(request, data=None, status_code=204)


@router.delete(
    "/groups/{conversation_id}",
    status_code=204,
    dependencies=[Depends(rate_limit("10/minute", scope="group_conversation_delete"))],
)
async def delete_group(
    request: Request,
    conversation_id: str,
    user=Depends(require_verified_user),
    service: ConversationsService = Depends(get_conversations_service),
):
    await service.delete_group(user_id=user.str_id, conversation_id=conversation_id)
    return ok(request, data=None, status_code=204)


@router.patch(
    "/groups/{conversation_id}",
    response_model=SuccessResponse[ConversationView],
    dependencies=[Depends(rate_limit("20/minute", scope="group_conversation_rename"))],
)
async def rename_group(
    request: Request,
    conversation_id: str,
    body: UpdateGroupRequest,
    user=Depends(require_verified_user),
    service: ConversationsService = Depends(get_conversations_service),
):
    conversation = await service.rename_group(
        actor_user_id=user.str_id,
        conversation_id=conversation_id,
        title=body.title,
    )
    data = (
        await service.views_for_user(user_id=user.str_id, conversations=[conversation])
    )[0]
    return ok(request, data=data)


@router.patch(
    "/{conversation_id}/settings",
    response_model=SuccessResponse[ConversationView],
    dependencies=[Depends(rate_limit("30/minute", scope="conversation_settings"))],
)
async def update_conversation_settings(
    request: Request,
    conversation_id: str,
    body: UpdateConversationSettingsRequest,
    user=Depends(require_verified_user),
    service: ConversationsService = Depends(get_conversations_service),
):
    conversation = await service.set_allow_member_polls(
        actor_user_id=user.str_id,
        conversation_id=conversation_id,
        allow=body.allow_member_polls,
    )
    data = (
        await service.views_for_user(user_id=user.str_id, conversations=[conversation])
    )[0]
    return ok(request, data=data)


@router.patch(
    "/groups/{conversation_id}/avatar",
    response_model=SuccessResponse[ConversationView],
    dependencies=[Depends(rate_limit("20/minute", scope="group_conversation_avatar"))],
)
async def set_group_avatar(
    request: Request,
    conversation_id: str,
    file: UploadFile = File(...),
    user=Depends(require_verified_user),
    service: ConversationsService = Depends(get_conversations_service),
):
    conversation = await service.set_group_avatar(
        actor_user_id=user.str_id,
        conversation_id=conversation_id,
        file=file,
    )
    data = (
        await service.views_for_user(user_id=user.str_id, conversations=[conversation])
    )[0]
    return ok(request, data=data)


@router.delete(
    "/groups/{conversation_id}/avatar",
    response_model=SuccessResponse[ConversationView],
    dependencies=[Depends(rate_limit("20/minute", scope="group_conversation_avatar"))],
)
async def remove_group_avatar(
    request: Request,
    conversation_id: str,
    user=Depends(require_verified_user),
    service: ConversationsService = Depends(get_conversations_service),
):
    conversation = await service.remove_group_avatar(
        actor_user_id=user.str_id,
        conversation_id=conversation_id,
    )
    data = (
        await service.views_for_user(user_id=user.str_id, conversations=[conversation])
    )[0]
    return ok(request, data=data)


@router.get(
    "/{conversation_id}/messages",
    response_model=PaginatedResponse[list[MessageDoc]],
    dependencies=[Depends(rate_limit("30/minute", scope="conversation_messages"))],
)
async def list_messages(
    request: Request,
    conversation_id: str,
    limit: int = Query(20, ge=1, le=100),
    cursor: Optional[str] = Query(None),
    user=Depends(require_verified_user),
    service: ConversationsService = Depends(get_conversations_service),
    messages: MessagesService = Depends(get_messages_service),
):
    await service.require_participant(
        user_id=user.str_id, conversation_id=conversation_id
    )
    items, next_cursor = await messages.get_history(
        user_id=user.str_id,
        container_type="conversation",
        container_id=conversation_id,
        limit=limit,
        cursor=cursor,
    )
    return ok_paginated(
        request,
        data=items,
        meta=PaginationMeta(cursor=cursor, next_cursor=next_cursor, limit=limit),
    )


@router.post(
    "/{conversation_id}/messages/text",
    status_code=201,
    response_model=SuccessResponse[MessageDoc],
    dependencies=[Depends(rate_limit("60/minute", scope="text_message"))],
)
async def send_text(
    request: Request,
    conversation_id: str,
    body: ConversationSendTextRequest,
    sio: Annotated[socketio.AsyncServer, Depends(get_sio)],
    user=Depends(require_verified_user),
    service: ConversationsService = Depends(get_conversations_service),
    messages: MessagesService = Depends(get_messages_service),
    notifications: NotificationsService = Depends(get_notifications_service),
):
    conversation = await service.require_can_post(
        user_id=user.str_id, conversation_id=conversation_id
    )
    result = await messages.send_text(
        sender_id=user.str_id,
        container_type="conversation",
        container_id=conversation_id,
        text=body.text,
        reply_mode=body.reply_mode,
        reply_to_message_id=body.reply_to_message_id,
    )
    await emit_send_result(
        sio,
        result=result,
        participant_ids=[
            str(participant_id) for participant_id in conversation.participant_ids
        ],
    )
    await emit_message_notifications(
        sio,
        notifications=notifications,
        message=result.message,
    )
    await service.clear_draft(user_id=user.str_id, conversation_id=conversation_id)
    await service.resurface_on_send(
        user_id=user.str_id, conversation_id=conversation_id
    )
    return ok(request, data=result.message, status_code=201)


@router.post(
    "/{conversation_id}/messages/media",
    status_code=201,
    response_model=SuccessResponse[MessageDoc],
    dependencies=[Depends(rate_limit("30/minute", scope="media_upload"))],
)
async def send_media(
    request: Request,
    conversation_id: str,
    sio: Annotated[socketio.AsyncServer, Depends(get_sio)],
    type: Literal["media", "file"] = Form(...),
    media_kind: Optional[Literal["voice", "audio", "image", "video"]] = Form(None),
    duration_ms: Optional[int] = Form(None),
    text: Optional[str] = Form(None),
    reply_mode: Optional[Literal["quote", "thread"]] = Form(None),
    reply_to_message_id: Optional[str] = Form(None),
    file: UploadFile = File(...),
    user=Depends(require_verified_user),
    service: ConversationsService = Depends(get_conversations_service),
    messages: MessagesService = Depends(get_messages_service),
    notifications: NotificationsService = Depends(get_notifications_service),
):
    conversation = await service.require_can_post(
        user_id=user.str_id, conversation_id=conversation_id
    )
    result = await messages.upload_media(
        sender_id=user.str_id,
        container_type="conversation",
        container_id=conversation_id,
        message_type=type,
        media_kind=media_kind,
        file=file,
        text=text,
        duration_ms=duration_ms,
        reply_mode=reply_mode,
        reply_to_message_id=reply_to_message_id,
    )
    await emit_send_result(
        sio,
        result=result,
        participant_ids=[
            str(participant_id) for participant_id in conversation.participant_ids
        ],
    )
    await emit_message_notifications(
        sio,
        notifications=notifications,
        message=result.message,
    )
    await service.resurface_on_send(
        user_id=user.str_id, conversation_id=conversation_id
    )
    return ok(request, data=result.message, status_code=201)


@router.post(
    "/{conversation_id}/messages/content",
    status_code=201,
    response_model=SuccessResponse[MessageDoc],
    dependencies=[Depends(rate_limit("60/minute", scope="rich_message"))],
)
async def send_rich_content(
    request: Request,
    conversation_id: str,
    body: SendRichContentRequest,
    sio: Annotated[socketio.AsyncServer, Depends(get_sio)],
    user=Depends(require_verified_user),
    service: ConversationsService = Depends(get_conversations_service),
    messages: MessagesService = Depends(get_messages_service),
    notifications: NotificationsService = Depends(get_notifications_service),
):
    conversation = await service.require_can_post(
        user_id=user.str_id, conversation_id=conversation_id
    )
    result = await messages.send_rich_content(
        sender_id=user.str_id,
        container_type="conversation",
        container_id=conversation_id,
        body=body,
    )
    await emit_send_result(
        sio,
        result=result,
        participant_ids=[
            str(participant_id) for participant_id in conversation.participant_ids
        ],
    )
    await emit_message_notifications(
        sio,
        notifications=notifications,
        message=result.message,
    )
    await service.clear_draft(user_id=user.str_id, conversation_id=conversation_id)
    await service.resurface_on_send(
        user_id=user.str_id, conversation_id=conversation_id
    )
    return ok(request, data=result.message, status_code=201)


@router.get(
    "/{conversation_id}/messages/{message_id}/thread",
    response_model=SuccessResponse[list[MessageDoc]],
)
async def get_thread(
    request: Request,
    conversation_id: str,
    message_id: str,
    user=Depends(require_verified_user),
    service: ConversationsService = Depends(get_conversations_service),
    messages: MessagesService = Depends(get_messages_service),
):
    await service.require_participant(
        user_id=user.str_id, conversation_id=conversation_id
    )
    items = await messages.get_thread(
        container_type="conversation",
        container_id=conversation_id,
        message_id=message_id,
        user_id=user.str_id,
    )
    return ok(request, data=items)


@router.get(
    "/{conversation_id}/messages/{message_id}/thread-summary",
    response_model=SuccessResponse[ThreadSummary],
)
async def get_thread_summary(
    request: Request,
    conversation_id: str,
    message_id: str,
    user=Depends(require_verified_user),
    service: ConversationsService = Depends(get_conversations_service),
    messages: MessagesService = Depends(get_messages_service),
):
    await service.require_participant(
        user_id=user.str_id, conversation_id=conversation_id
    )
    summary = await messages.get_thread_summary(
        container_type="conversation",
        container_id=conversation_id,
        message_id=message_id,
        user_id=user.str_id,
    )
    return ok(request, data=summary)


@router.post(
    "/{conversation_id}/messages/{message_id}/delivered",
    response_model=SuccessResponse[MessageDoc],
)
async def mark_message_delivered(
    request: Request,
    conversation_id: str,
    message_id: str,
    sio: Annotated[socketio.AsyncServer, Depends(get_sio)],
    user=Depends(require_verified_user),
    service: ConversationsService = Depends(get_conversations_service),
    messages: MessagesService = Depends(get_messages_service),
):
    conversation = await service.require_participant(
        user_id=user.str_id, conversation_id=conversation_id
    )
    message = await messages.mark_delivered(
        container_type="conversation",
        container_id=conversation_id,
        message_id=message_id,
        user_id=user.str_id,
    )
    payload = {
        "message_id": message.id,
        "container_type": message.container_type,
        "container_id": message.container_id,
        "conversation_id": conversation_id,
        "receipt_summary": message.receipt_summary.model_dump(mode="json"),
        "updated_at": message.updated_at,
    }
    for participant_id in conversation.participant_ids:
        await emit_to_user(sio, str(participant_id), "message_status", payload)
    return ok(request, data=message)


@router.post(
    "/{conversation_id}/messages/{message_id}/read",
    response_model=SuccessResponse[MessageDoc],
)
async def mark_message_read(
    request: Request,
    conversation_id: str,
    message_id: str,
    sio: Annotated[socketio.AsyncServer, Depends(get_sio)],
    user=Depends(require_verified_user),
    service: ConversationsService = Depends(get_conversations_service),
    messages: MessagesService = Depends(get_messages_service),
):
    conversation = await service.require_participant(
        user_id=user.str_id, conversation_id=conversation_id
    )
    await service.mark_conversation_read(
        user_id=user.str_id,
        conversation_id=conversation_id,
        last_read_message_id=message_id,
    )
    message = await messages.mark_read(
        container_type="conversation",
        container_id=conversation_id,
        message_id=message_id,
        user_id=user.str_id,
    )
    payload = {
        "message_id": message.id,
        "container_type": message.container_type,
        "container_id": message.container_id,
        "conversation_id": conversation_id,
        "receipt_summary": message.receipt_summary.model_dump(mode="json"),
        "updated_at": message.updated_at,
    }
    for participant_id in conversation.participant_ids:
        await emit_to_user(sio, str(participant_id), "message_status", payload)
    return ok(request, data=message)


@router.post(
    "/{conversation_id}/messages/{message_id}/reactions",
    response_model=SuccessResponse[MessageDoc],
)
async def add_reaction(
    request: Request,
    conversation_id: str,
    message_id: str,
    body: AddReactionRequest,
    sio: Annotated[socketio.AsyncServer, Depends(get_sio)],
    user=Depends(require_verified_user),
    service: ConversationsService = Depends(get_conversations_service),
    messages: MessagesService = Depends(get_messages_service),
):
    conversation = await service.require_participant(
        user_id=user.str_id, conversation_id=conversation_id
    )
    message = await messages.add_reaction(
        container_type="conversation",
        container_id=conversation_id,
        message_id=message_id,
        user_id=user.str_id,
        emoji=body.emoji,
    )
    payload = {
        "message_id": message.id,
        "container_type": message.container_type,
        "container_id": message.container_id,
        "conversation_id": conversation_id,
        "reactions": [
            reaction.model_dump(mode="json") for reaction in message.reactions
        ],
        "updated_at": message.updated_at,
    }
    for participant_id in conversation.participant_ids:
        await emit_to_user(sio, str(participant_id), "message_reacted", payload)
    return ok(request, data=message)


@router.delete(
    "/{conversation_id}/messages/{message_id}/reactions/{emoji}/me",
    response_model=SuccessResponse[MessageDoc],
)
async def remove_reaction(
    request: Request,
    conversation_id: str,
    message_id: str,
    emoji: str,
    sio: Annotated[socketio.AsyncServer, Depends(get_sio)],
    user=Depends(require_verified_user),
    service: ConversationsService = Depends(get_conversations_service),
    messages: MessagesService = Depends(get_messages_service),
):
    conversation = await service.require_participant(
        user_id=user.str_id, conversation_id=conversation_id
    )
    message = await messages.remove_reaction(
        container_type="conversation",
        container_id=conversation_id,
        message_id=message_id,
        user_id=user.str_id,
        emoji=emoji,
    )
    payload = {
        "message_id": message.id,
        "container_type": message.container_type,
        "container_id": message.container_id,
        "conversation_id": conversation_id,
        "reactions": [
            reaction.model_dump(mode="json") for reaction in message.reactions
        ],
        "updated_at": message.updated_at,
    }
    for participant_id in conversation.participant_ids:
        await emit_to_user(sio, str(participant_id), "message_reacted", payload)
    return ok(request, data=message)


@router.patch(
    "/{conversation_id}/messages/{message_id}",
    response_model=SuccessResponse[MessageDoc],
)
async def edit_message(
    request: Request,
    conversation_id: str,
    message_id: str,
    body: EditMessageRequest,
    sio: Annotated[socketio.AsyncServer, Depends(get_sio)],
    user=Depends(require_verified_user),
    service: ConversationsService = Depends(get_conversations_service),
    messages: MessagesService = Depends(get_messages_service),
):
    conversation = await service.require_participant(
        user_id=user.str_id, conversation_id=conversation_id
    )
    message = await messages.edit_text_message_in_container(
        container_type="conversation",
        container_id=conversation_id,
        message_id=message_id,
        sender_id=user.str_id,
        text=body.text,
    )
    payload = message.model_dump(mode="json")
    for participant_id in conversation.participant_ids:
        await emit_to_user(sio, str(participant_id), "message_edited", payload)
    return ok(request, data=message)


@router.post(
    "/{conversation_id}/messages/{message_id}/pin",
    response_model=SuccessResponse[ConversationView],
    responses=build_error_responses(403, 404),
    dependencies=[Depends(rate_limit("30/minute", scope="message_pin"))],
)
async def pin_message(
    request: Request,
    conversation_id: str,
    message_id: str,
    sio: Annotated[socketio.AsyncServer, Depends(get_sio)],
    user=Depends(require_verified_user),
    service: ConversationsService = Depends(get_conversations_service),
):
    conversation = await service.pin_message(
        user_id=user.str_id, conversation_id=conversation_id, message_id=message_id
    )
    payload = {
        "conversation_id": conversation.str_id,
        "pinned_message_ids": conversation.pinned_message_ids,
    }
    for participant_id in conversation.participant_ids:
        await emit_to_user(
            sio, str(participant_id), "conversation_pins_updated", payload
        )
    data = (
        await service.views_for_user(user_id=user.str_id, conversations=[conversation])
    )[0]
    return ok(request, data=data)


@router.delete(
    "/{conversation_id}/messages/{message_id}/pin",
    response_model=SuccessResponse[ConversationView],
    responses=build_error_responses(403, 404),
    dependencies=[Depends(rate_limit("30/minute", scope="message_unpin"))],
)
async def unpin_message(
    request: Request,
    conversation_id: str,
    message_id: str,
    sio: Annotated[socketio.AsyncServer, Depends(get_sio)],
    user=Depends(require_verified_user),
    service: ConversationsService = Depends(get_conversations_service),
):
    conversation = await service.unpin_message(
        user_id=user.str_id, conversation_id=conversation_id, message_id=message_id
    )
    payload = {
        "conversation_id": conversation.str_id,
        "pinned_message_ids": conversation.pinned_message_ids,
    }
    for participant_id in conversation.participant_ids:
        await emit_to_user(
            sio, str(participant_id), "conversation_pins_updated", payload
        )
    data = (
        await service.views_for_user(user_id=user.str_id, conversations=[conversation])
    )[0]
    return ok(request, data=data)


@router.get(
    "/{conversation_id}/pinned-messages",
    response_model=SuccessResponse[list[MessageDoc]],
    dependencies=[Depends(rate_limit("30/minute", scope="pinned_messages"))],
)
async def list_pinned_messages(
    request: Request,
    conversation_id: str,
    user=Depends(require_verified_user),
    service: ConversationsService = Depends(get_conversations_service),
    messages: MessagesService = Depends(get_messages_service),
):
    pinned_ids = await service.list_pinned_message_ids(
        user_id=user.str_id, conversation_id=conversation_id
    )
    items = await messages.get_messages_by_ids(
        container_type="conversation",
        container_id=conversation_id,
        message_ids=pinned_ids,
        user_id=user.str_id,
    )
    return ok(request, data=items)


@router.post(
    "/{conversation_id}/messages/{message_id}/forward",
    status_code=201,
    response_model=SuccessResponse[MessageDoc],
    responses=build_error_responses(403, 404),
    dependencies=[Depends(rate_limit("30/minute", scope="message_forward"))],
)
async def forward_message(
    request: Request,
    conversation_id: str,
    message_id: str,
    body: ForwardMessageRequest,
    sio: Annotated[socketio.AsyncServer, Depends(get_sio)],
    user=Depends(require_verified_user),
    service: ConversationsService = Depends(get_conversations_service),
    messages: MessagesService = Depends(get_messages_service),
    notifications: NotificationsService = Depends(get_notifications_service),
):
    await service.require_participant(
        user_id=user.str_id, conversation_id=conversation_id
    )
    target = await service.require_can_post(
        user_id=user.str_id, conversation_id=body.target_conversation_id
    )
    result = await messages.forward_message(
        source_container_type="conversation",
        source_container_id=conversation_id,
        message_id=message_id,
        target_container_type="conversation",
        target_container_id=body.target_conversation_id,
        sender_id=user.str_id,
    )
    await emit_send_result(
        sio,
        result=result,
        participant_ids=[str(pid) for pid in target.participant_ids],
    )
    await emit_message_notifications(
        sio, notifications=notifications, message=result.message
    )
    return ok(request, data=result.message, status_code=201)


@router.post(
    "/{conversation_id}/messages/schedule",
    status_code=201,
    response_model=SuccessResponse[MessageDoc],
    responses=build_error_responses(400, 403, 404),
    dependencies=[Depends(rate_limit("30/minute", scope="message_schedule"))],
)
async def schedule_message(
    request: Request,
    conversation_id: str,
    body: ScheduleMessageRequest,
    user=Depends(require_verified_user),
    service: ConversationsService = Depends(get_conversations_service),
    messages: MessagesService = Depends(get_messages_service),
):
    await service.require_can_post(user_id=user.str_id, conversation_id=conversation_id)
    message = await messages.schedule_message(
        container_type="conversation",
        container_id=conversation_id,
        sender_id=user.str_id,
        text=body.text,
        scheduled_for=body.scheduled_for,
    )
    return ok(request, data=message, status_code=201)


@router.get(
    "/{conversation_id}/messages/scheduled",
    response_model=SuccessResponse[list[MessageDoc]],
    dependencies=[Depends(rate_limit("30/minute", scope="scheduled_messages"))],
)
async def list_scheduled_messages(
    request: Request,
    conversation_id: str,
    user=Depends(require_verified_user),
    service: ConversationsService = Depends(get_conversations_service),
    messages: MessagesService = Depends(get_messages_service),
):
    await service.require_participant(
        user_id=user.str_id, conversation_id=conversation_id
    )
    items = await messages.list_scheduled_messages(
        container_type="conversation",
        container_id=conversation_id,
        sender_id=user.str_id,
    )
    return ok(request, data=items)


@router.delete(
    "/{conversation_id}/messages/scheduled/{message_id}",
    status_code=204,
    responses=build_error_responses(404),
    dependencies=[Depends(rate_limit("30/minute", scope="scheduled_message_cancel"))],
)
async def cancel_scheduled_message(
    request: Request,
    conversation_id: str,
    message_id: str,
    user=Depends(require_verified_user),
    service: ConversationsService = Depends(get_conversations_service),
    messages: MessagesService = Depends(get_messages_service),
):
    await service.require_participant(
        user_id=user.str_id, conversation_id=conversation_id
    )
    await messages.cancel_scheduled_message(
        container_type="conversation",
        container_id=conversation_id,
        message_id=message_id,
        sender_id=user.str_id,
    )
    return ok(request, data=None, status_code=204)


@router.get(
    "/{conversation_id}/messages/{message_id}",
    response_model=SuccessResponse[MessageDoc],
    responses=build_error_responses(404),
    dependencies=[Depends(rate_limit("60/minute", scope="conversation_message_get"))],
)
async def get_message(
    request: Request,
    conversation_id: str,
    message_id: str,
    user=Depends(require_verified_user),
    service: ConversationsService = Depends(get_conversations_service),
    messages: MessagesService = Depends(get_messages_service),
):
    await service.require_participant(
        user_id=user.str_id, conversation_id=conversation_id
    )
    message = await messages.get_message(
        container_type="conversation",
        container_id=conversation_id,
        message_id=message_id,
        user_id=user.str_id,
    )
    return ok(request, data=message)


@router.put(
    "/{conversation_id}/draft",
    response_model=SuccessResponse[ParticipantView],
    dependencies=[Depends(rate_limit("120/minute", scope="conversation_draft_set"))],
)
async def set_draft(
    request: Request,
    conversation_id: str,
    body: SetDraftRequest,
    user=Depends(require_verified_user),
    service: ConversationsService = Depends(get_conversations_service),
):
    participant = await service.set_draft(
        user_id=user.str_id, conversation_id=conversation_id, text=body.text
    )
    return ok(request, data=to_participant_view(participant))


@router.get(
    "/{conversation_id}/draft",
    response_model=SuccessResponse[ParticipantView],
    dependencies=[Depends(rate_limit("120/minute", scope="conversation_draft_get"))],
)
async def get_draft(
    request: Request,
    conversation_id: str,
    user=Depends(require_verified_user),
    service: ConversationsService = Depends(get_conversations_service),
):
    participant = await service.get_draft(
        user_id=user.str_id, conversation_id=conversation_id
    )
    return ok(request, data=to_participant_view(participant))


@router.delete(
    "/{conversation_id}/draft",
    status_code=204,
    dependencies=[Depends(rate_limit("120/minute", scope="conversation_draft_clear"))],
)
async def clear_draft(
    request: Request,
    conversation_id: str,
    user=Depends(require_verified_user),
    service: ConversationsService = Depends(get_conversations_service),
):
    await service.clear_draft(user_id=user.str_id, conversation_id=conversation_id)
    return ok(request, data=None, status_code=204)


@router.delete(
    "/{conversation_id}/messages/all",
    response_model=SuccessResponse[ClearChatResponse],
    dependencies=[Depends(rate_limit("10/minute", scope="conversation_clear_all"))],
)
async def clear_messages_for_everyone(
    request: Request,
    conversation_id: str,
    sio: Annotated[socketio.AsyncServer, Depends(get_sio)],
    user=Depends(require_verified_user),
    service: ConversationsService = Depends(get_conversations_service),
    messages: MessagesService = Depends(get_messages_service),
):
    conversation = await service.require_group_manager(
        user_id=user.str_id, conversation_id=conversation_id
    )
    conv_id, count = await messages.clear_chat_history_for_everyone(
        container_type="conversation",
        container_id=conversation_id,
    )
    payload = {"conversation_id": conv_id, "cleared_count": count}
    for participant_id in conversation.participant_ids:
        await emit_to_user(
            sio, str(participant_id), "conversation_history_cleared", payload
        )
    return ok(
        request,
        data=ClearChatResponse(conversation_id=conv_id, cleared_count=count),
    )


@router.delete(
    "/{conversation_id}/messages/{message_id}",
    response_model=SuccessResponse[DeleteMessageResponse],
)
async def delete_message(
    request: Request,
    conversation_id: str,
    message_id: str,
    sio: Annotated[socketio.AsyncServer, Depends(get_sio)],
    user=Depends(require_verified_user),
    service: ConversationsService = Depends(get_conversations_service),
    messages: MessagesService = Depends(get_messages_service),
):
    conversation = await service.require_participant(
        user_id=user.str_id, conversation_id=conversation_id
    )
    outcome = await messages.delete_message(
        container_type="conversation",
        container_id=conversation_id,
        message_id=message_id,
        actor_user_id=user.str_id,
    )
    payload = outcome.response.model_dump(mode="json")
    if outcome.response.deleted_for_everyone:
        for participant_id in conversation.participant_ids:
            await emit_to_user(sio, str(participant_id), "message_deleted", payload)
    else:
        await emit_to_user(sio, user.str_id, "message_deleted", payload)
    return ok(request, data=outcome.response)


@router.delete(
    "/{conversation_id}/messages",
    response_model=SuccessResponse[ClearChatResponse],
    dependencies=[Depends(rate_limit("30/minute", scope="conversation_clear"))],
)
async def clear_messages(
    request: Request,
    conversation_id: str,
    user=Depends(require_verified_user),
    service: ConversationsService = Depends(get_conversations_service),
    messages: MessagesService = Depends(get_messages_service),
):
    conv_id, count = await messages.clear_chat_history(
        container_type="conversation",
        container_id=conversation_id, user_id=user.str_id
    )
    return ok(
        request,
        data=ClearChatResponse(conversation_id=conv_id, cleared_count=count),
    )


@router.delete(
    "/{conversation_id}",
    response_model=SuccessResponse[DeleteChatResponse],
    dependencies=[Depends(rate_limit("30/minute", scope="conversation_delete"))],
)
async def delete_conversation(
    request: Request,
    conversation_id: str,
    user=Depends(require_verified_user),
    service: ConversationsService = Depends(get_conversations_service),
    messages: MessagesService = Depends(get_messages_service),
):
    peer_id = await service.get_dm_peer(
        user_id=user.str_id, conversation_id=conversation_id
    )
    conv_id, count, ping_deleted = await messages.delete_chat(
        container_type="conversation",
        container_id=conversation_id, user_id=user.str_id, peer_user_id=peer_id
    )
    return ok(
        request,
        data=DeleteChatResponse(
            conversation_id=conv_id, cleared_count=count, ping_deleted=ping_deleted
        ),
    )
