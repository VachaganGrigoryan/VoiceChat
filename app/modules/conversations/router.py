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
    to_participant_view,
)
from app.modules.conversations.schemas import (
    AddGroupMembersRequest,
    ConversationSendTextRequest,
    ConversationView,
    CreateGroupRequest,
    CreateDmRequest,
    ParticipantView,
    TransferOwnershipRequest,
    UpdateGroupRequest,
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
    MessageDoc,
    ThreadSummary,
)
from app.modules.messages.service import MessagesService
from app.modules.realtime import emit_to_user

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
    data = (await service.views_for_user(user_id=user.str_id, conversations=[conversation]))[0]
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
    )
    data = (await service.views_for_user(user_id=user.str_id, conversations=[conversation]))[0]
    return ok(request, data=data, status_code=201)


@router.get(
    "",
    response_model=PaginatedResponse[list[ConversationView]],
    dependencies=[Depends(rate_limit("30/minute", scope="conversations_list"))],
)
async def list_conversations(
    request: Request,
    limit: int = Query(50, ge=1, le=100),
    cursor: Optional[str] = Query(None),
    user=Depends(require_verified_user),
    service: ConversationsService = Depends(get_conversations_service),
):
    items, next_cursor = await service.list_for_user(
        user_id=user.str_id, limit=limit, cursor=cursor
    )
    data = await service.views_for_user(user_id=user.str_id, conversations=items)
    return ok_paginated(
        request,
        data=data,
        meta=PaginationMeta(cursor=cursor, next_cursor=next_cursor, limit=limit),
    )


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
        await service.views_for_user(
            user_id=user.str_id, conversations=[conversation]
        )
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
        await service.views_for_user(
            user_id=user.str_id, conversations=[conversation]
        )
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
        await service.views_for_user(
            user_id=user.str_id, conversations=[conversation]
        )
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
    items, next_cursor = await messages.get_conversation_history(
        user_id=user.str_id,
        conversation_id=conversation_id,
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
):
    conversation = await service.require_participant(
        user_id=user.str_id, conversation_id=conversation_id
    )
    result = await messages.send_text_to_conversation(
        sender_id=user.str_id,
        conversation_id=conversation_id,
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
):
    conversation = await service.require_participant(
        user_id=user.str_id, conversation_id=conversation_id
    )
    result = await messages.upload_media_to_conversation(
        sender_id=user.str_id,
        conversation_id=conversation_id,
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
    items = await messages.get_thread_for_conversation(
        conversation_id=conversation_id,
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
    summary = await messages.get_thread_summary_for_conversation(
        conversation_id=conversation_id,
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
    message = await messages.mark_delivered_for_conversation(
        conversation_id=conversation_id,
        message_id=message_id,
        user_id=user.str_id,
    )
    payload = {
        "message_id": message.id,
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
    message = await messages.mark_read_for_conversation(
        conversation_id=conversation_id,
        message_id=message_id,
        user_id=user.str_id,
    )
    payload = {
        "message_id": message.id,
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
    message = await messages.add_reaction_for_conversation(
        conversation_id=conversation_id,
        message_id=message_id,
        user_id=user.str_id,
        emoji=body.emoji,
    )
    payload = {
        "message_id": message.id,
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
    message = await messages.remove_reaction_for_conversation(
        conversation_id=conversation_id,
        message_id=message_id,
        user_id=user.str_id,
        emoji=emoji,
    )
    payload = {
        "message_id": message.id,
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
    message = await messages.edit_text_message_for_conversation(
        conversation_id=conversation_id,
        message_id=message_id,
        sender_id=user.str_id,
        text=body.text,
    )
    payload = message.model_dump(mode="json")
    for participant_id in conversation.participant_ids:
        await emit_to_user(sio, str(participant_id), "message_edited", payload)
    return ok(request, data=message)


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
        user_id=user.str_id,
        conversation_id=conversation_id,
        allowed_roles={"owner", "admin"},
    )
    conv_id, count = await messages.clear_chat_history_for_everyone(
        conversation_id=conversation_id
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
    outcome = await messages.delete_message_for_conversation(
        conversation_id=conversation_id,
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
        conversation_id=conversation_id, user_id=user.str_id
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
        conversation_id=conversation_id, user_id=user.str_id, peer_user_id=peer_id
    )
    return ok(
        request,
        data=DeleteChatResponse(
            conversation_id=conv_id, cleared_count=count, ping_deleted=ping_deleted
        ),
    )
