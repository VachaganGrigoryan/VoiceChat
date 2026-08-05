from __future__ import annotations

from typing import Annotated, Optional

import socketio
from fastapi import APIRouter, Depends, File, Query, UploadFile
from starlette.requests import Request

from app.core.deps import get_sio
from app.modules.realtime.emits import (
    emit_capabilities_invalidated,
    emit_resource_deleted,
)
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
    ConversationView,
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
from app.modules.relationships.schemas import to_relationship_view
from app.modules.messages.dependencies import get_messages_service
from app.modules.messages.schemas import DeleteChatResponse, SetDraftRequest
from app.modules.messages.service import MessagesService

router = APIRouter(
    prefix="/conversations",
    tags=["conversations"],
    responses=build_error_responses(400, 401, 422, 500),
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
    status, conversation, membership = await service.redeem_invite(
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
            membership=to_relationship_view(membership),
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
        approval_required=body.approval_required,
        role_ids=body.role_ids,
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
    sio: Annotated[socketio.AsyncServer, Depends(get_sio)],
    user=Depends(require_verified_user),
    service: ConversationsService = Depends(get_conversations_service),
):
    recipients = await service.delete_group(
        user_id=user.str_id, conversation_id=conversation_id
    )
    await emit_resource_deleted(
        sio,
        to_user_ids=recipients,
        resource_type="conversation",
        resource_id=conversation_id,
    )
    await emit_capabilities_invalidated(
        sio,
        to_user_ids=recipients,
        resource_type="conversation",
        resource_id=conversation_id,
    )
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
    await service.get_dm_peer(user_id=user.str_id, conversation_id=conversation_id)
    conv_id, count = await messages.delete_chat(
        container_type="conversation",
        container_id=conversation_id,
        user_id=user.str_id,
    )
    return ok(
        request,
        data=DeleteChatResponse(
            conversation_id=conv_id,
            cleared_count=count,
        ),
    )
