from __future__ import annotations

from typing import Annotated, Literal, Optional

import socketio
from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile

from app.core.rate_limit import rate_limit
from app.core.deps import get_sio
from app.db.models.embedded import TextStyleDocument
from app.core.errors.openapi import build_error_responses
from app.core.http import (
    PaginatedResponse,
    PaginationMeta,
    SuccessResponse,
    ok,
    ok_paginated,
)
from app.core.security import require_verified_user
from app.modules.conversations.dependencies import get_conversations_service
from app.modules.conversations.schemas import (
    ConversationSendTextRequest,
    ConversationView,
)
from app.modules.conversations.service import ConversationsService
from app.modules.messages.dependencies import (
    MessageContext,
    get_messages_service,
    require_message_access,
)
from app.modules.messages.emit_helpers import (
    emit_message_notifications,
    emit_send_result,
    emit_to_container,
)
from app.modules.messages.schemas import (
    AddReactionRequest,
    ClearChatResponse,
    DeleteMessageResponse,
    EditMessageRequest,
    ForwardMessageRequest,
    MessageDoc,
    ScheduleMessageRequest,
    SendRichContentRequest,
    ThreadSummary,
)
from app.modules.messages.service import MessagesService
from app.modules.notifications.dependencies import get_notifications_service
from app.modules.notifications.service import NotificationsService
from app.modules.realtime import emit_to_user
from app.modules.relationships.dependencies import get_relationship_service
from app.modules.relationships.service import RelationshipService

router = APIRouter(
    prefix="/messages",
    tags=["messages"],
    responses=build_error_responses(400, 401, 422, 500),
)

conversation_messages_router = APIRouter(
    prefix="/conversations",
    tags=["messages"],
    responses=build_error_responses(400, 401, 422, 500),
)


# ============================================================================
# Collection Operations (nested under /conversations/{conversation_id}/messages)
# ============================================================================


@conversation_messages_router.get(
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


@conversation_messages_router.post(
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
        style=(
            TextStyleDocument(**body.style.model_dump()) if body.style else None
        ),
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


@conversation_messages_router.post(
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


@conversation_messages_router.post(
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


@conversation_messages_router.delete(
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
        container_id=conversation_id,
        user_id=user.str_id,
    )
    return ok(
        request,
        data=ClearChatResponse(conversation_id=conv_id, cleared_count=count),
    )


@conversation_messages_router.delete(
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


@conversation_messages_router.post(
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


@conversation_messages_router.get(
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


@conversation_messages_router.delete(
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


@conversation_messages_router.get(
    "/{conversation_id}/messages/pinned",
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


# ============================================================================
# Item Operations (flat surface at /messages/{message_id}...)
# ============================================================================


@router.get(
    "/{message_id}",
    response_model=SuccessResponse[MessageDoc],
    responses=build_error_responses(403, 404),
    dependencies=[Depends(rate_limit("60/minute", scope="message_get"))],
)
async def get_message(
    request: Request,
    ctx: MessageContext = Depends(require_message_access),
):
    return ok(request, data=ctx.message)


@router.patch(
    "/{message_id}",
    response_model=SuccessResponse[MessageDoc],
    responses=build_error_responses(403, 404),
)
async def edit_message(
    request: Request,
    body: EditMessageRequest,
    sio: Annotated[socketio.AsyncServer, Depends(get_sio)],
    user=Depends(require_verified_user),
    ctx: MessageContext = Depends(require_message_access),
    messages: MessagesService = Depends(get_messages_service),
    rel_service: RelationshipService = Depends(get_relationship_service),
):
    updated = await messages.edit_text_message_in_container(
        container_type=ctx.container_type,
        container_id=ctx.container_id,
        message_id=ctx.message.str_id,
        sender_id=user.str_id,
        text=body.text,
    )
    payload = updated.model_dump(mode="json")
    await emit_to_container(
        sio,
        container_type=ctx.container_type,
        container_id=ctx.container_id,
        conversation=ctx.conversation,
        relationships_service=rel_service,
        event="message_edited",
        payload=payload,
    )
    return ok(request, data=updated)


@router.delete(
    "/{message_id}",
    response_model=SuccessResponse[DeleteMessageResponse],
    responses=build_error_responses(403, 404),
)
async def delete_message(
    request: Request,
    sio: Annotated[socketio.AsyncServer, Depends(get_sio)],
    user=Depends(require_verified_user),
    ctx: MessageContext = Depends(require_message_access),
    messages: MessagesService = Depends(get_messages_service),
    rel_service: RelationshipService = Depends(get_relationship_service),
):
    outcome = await messages.delete_message(
        container_type=ctx.container_type,
        container_id=ctx.container_id,
        message_id=ctx.message.str_id,
        actor_user_id=user.str_id,
    )
    payload = outcome.response.model_dump(mode="json")
    if outcome.response.deleted_for_everyone:
        await emit_to_container(
            sio,
            container_type=ctx.container_type,
            container_id=ctx.container_id,
            conversation=ctx.conversation,
            relationships_service=rel_service,
            event="message_deleted",
            payload=payload,
        )
    else:
        await emit_to_user(sio, user.str_id, "message_deleted", payload)
    return ok(request, data=outcome.response)


@router.get(
    "/{message_id}/thread",
    response_model=SuccessResponse[list[MessageDoc]],
    responses=build_error_responses(403, 404),
)
async def get_thread(
    request: Request,
    user=Depends(require_verified_user),
    ctx: MessageContext = Depends(require_message_access),
    messages: MessagesService = Depends(get_messages_service),
):
    items = await messages.get_thread(
        container_type=ctx.container_type,
        container_id=ctx.container_id,
        message_id=ctx.message.str_id,
        user_id=user.str_id,
    )
    return ok(request, data=items)


@router.get(
    "/{message_id}/thread-summary",
    response_model=SuccessResponse[ThreadSummary],
    responses=build_error_responses(403, 404),
)
async def get_thread_summary(
    request: Request,
    user=Depends(require_verified_user),
    ctx: MessageContext = Depends(require_message_access),
    messages: MessagesService = Depends(get_messages_service),
):
    summary = await messages.get_thread_summary(
        container_type=ctx.container_type,
        container_id=ctx.container_id,
        message_id=ctx.message.str_id,
        user_id=user.str_id,
    )
    return ok(request, data=summary)


@router.post(
    "/{message_id}/delivered",
    response_model=SuccessResponse[MessageDoc],
    responses=build_error_responses(403, 404),
)
async def mark_message_delivered(
    request: Request,
    sio: Annotated[socketio.AsyncServer, Depends(get_sio)],
    user=Depends(require_verified_user),
    ctx: MessageContext = Depends(require_message_access),
    messages: MessagesService = Depends(get_messages_service),
    rel_service: RelationshipService = Depends(get_relationship_service),
):
    message = await messages.mark_delivered(
        container_type=ctx.container_type,
        container_id=ctx.container_id,
        message_id=ctx.message.str_id,
        user_id=user.str_id,
    )
    payload = {
        "message_id": message.id,
        "container_type": message.container_type,
        "container_id": message.container_id,
        "conversation_id": ctx.container_id if ctx.container_type == "conversation" else None,
        "receipt_summary": message.receipt_summary.model_dump(mode="json"),
        "updated_at": message.updated_at,
    }
    await emit_to_container(
        sio,
        container_type=ctx.container_type,
        container_id=ctx.container_id,
        conversation=ctx.conversation,
        relationships_service=rel_service,
        event="message_status",
        payload=payload,
    )
    return ok(request, data=message)


@router.post(
    "/{message_id}/read",
    response_model=SuccessResponse[MessageDoc],
    responses=build_error_responses(403, 404),
)
async def mark_message_read(
    request: Request,
    sio: Annotated[socketio.AsyncServer, Depends(get_sio)],
    user=Depends(require_verified_user),
    ctx: MessageContext = Depends(require_message_access),
    messages: MessagesService = Depends(get_messages_service),
    conversations_service: ConversationsService = Depends(get_conversations_service),
    rel_service: RelationshipService = Depends(get_relationship_service),
):
    if ctx.container_type == "conversation":
        await conversations_service.mark_conversation_read(
            user_id=user.str_id,
            conversation_id=ctx.container_id,
            last_read_message_id=ctx.message.str_id,
        )

    message = await messages.mark_read(
        container_type=ctx.container_type,
        container_id=ctx.container_id,
        message_id=ctx.message.str_id,
        user_id=user.str_id,
    )
    payload = {
        "message_id": message.id,
        "container_type": message.container_type,
        "container_id": message.container_id,
        "conversation_id": ctx.container_id if ctx.container_type == "conversation" else None,
        "receipt_summary": message.receipt_summary.model_dump(mode="json"),
        "updated_at": message.updated_at,
    }
    await emit_to_container(
        sio,
        container_type=ctx.container_type,
        container_id=ctx.container_id,
        conversation=ctx.conversation,
        relationships_service=rel_service,
        event="message_status",
        payload=payload,
    )
    return ok(request, data=message)


@router.post(
    "/{message_id}/reactions",
    response_model=SuccessResponse[MessageDoc],
    responses=build_error_responses(403, 404),
)
async def add_reaction(
    request: Request,
    body: AddReactionRequest,
    sio: Annotated[socketio.AsyncServer, Depends(get_sio)],
    user=Depends(require_verified_user),
    ctx: MessageContext = Depends(require_message_access),
    messages: MessagesService = Depends(get_messages_service),
    rel_service: RelationshipService = Depends(get_relationship_service),
):
    message = await messages.add_reaction(
        container_type=ctx.container_type,
        container_id=ctx.container_id,
        message_id=ctx.message.str_id,
        user_id=user.str_id,
        emoji=body.emoji,
    )
    payload = {
        "message_id": message.id,
        "container_type": message.container_type,
        "container_id": message.container_id,
        "conversation_id": ctx.container_id if ctx.container_type == "conversation" else None,
        "reactions": [
            reaction.model_dump(mode="json") for reaction in message.reactions
        ],
        "updated_at": message.updated_at,
    }
    await emit_to_container(
        sio,
        container_type=ctx.container_type,
        container_id=ctx.container_id,
        conversation=ctx.conversation,
        relationships_service=rel_service,
        event="message_reacted",
        payload=payload,
    )
    return ok(request, data=message)


@router.delete(
    "/{message_id}/reactions/{emoji}/me",
    response_model=SuccessResponse[MessageDoc],
    responses=build_error_responses(403, 404),
)
async def remove_reaction(
    request: Request,
    emoji: str,
    sio: Annotated[socketio.AsyncServer, Depends(get_sio)],
    user=Depends(require_verified_user),
    ctx: MessageContext = Depends(require_message_access),
    messages: MessagesService = Depends(get_messages_service),
    rel_service: RelationshipService = Depends(get_relationship_service),
):
    message = await messages.remove_reaction(
        container_type=ctx.container_type,
        container_id=ctx.container_id,
        message_id=ctx.message.str_id,
        user_id=user.str_id,
        emoji=emoji,
    )
    payload = {
        "message_id": message.id,
        "container_type": message.container_type,
        "container_id": message.container_id,
        "conversation_id": ctx.container_id if ctx.container_type == "conversation" else None,
        "reactions": [
            reaction.model_dump(mode="json") for reaction in message.reactions
        ],
        "updated_at": message.updated_at,
    }
    await emit_to_container(
        sio,
        container_type=ctx.container_type,
        container_id=ctx.container_id,
        conversation=ctx.conversation,
        relationships_service=rel_service,
        event="message_reacted",
        payload=payload,
    )
    return ok(request, data=message)


@router.post(
    "/{message_id}/forward",
    status_code=201,
    response_model=SuccessResponse[MessageDoc],
    responses=build_error_responses(400, 403, 404),
    dependencies=[Depends(rate_limit("30/minute", scope="message_forward"))],
)
async def forward_message(
    request: Request,
    body: ForwardMessageRequest,
    sio: Annotated[socketio.AsyncServer, Depends(get_sio)],
    user=Depends(require_verified_user),
    ctx: MessageContext = Depends(require_message_access),
    conversations_service: ConversationsService = Depends(get_conversations_service),
    messages: MessagesService = Depends(get_messages_service),
    notifications: NotificationsService = Depends(get_notifications_service),
):
    target = await conversations_service.require_can_post(
        user_id=user.str_id, conversation_id=body.target_conversation_id
    )
    result = await messages.forward_message(
        source_container_type=ctx.container_type,
        source_container_id=ctx.container_id,
        message_id=ctx.message.str_id,
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
    "/{message_id}/pin",
    response_model=SuccessResponse[ConversationView],
    responses=build_error_responses(400, 403, 404),
    dependencies=[Depends(rate_limit("30/minute", scope="message_pin"))],
)
async def pin_message(
    request: Request,
    sio: Annotated[socketio.AsyncServer, Depends(get_sio)],
    user=Depends(require_verified_user),
    ctx: MessageContext = Depends(require_message_access),
    service: ConversationsService = Depends(get_conversations_service),
):
    conversation = ctx.require_conversation_container()
    updated_conv = await service.pin_message(
        user_id=user.str_id,
        conversation_id=conversation.str_id,
        message_id=ctx.message.str_id,
    )
    payload = {
        "conversation_id": updated_conv.str_id,
        "pinned_message_ids": updated_conv.pinned_message_ids,
    }
    for participant_id in updated_conv.participant_ids:
        await emit_to_user(
            sio, str(participant_id), "conversation_pins_updated", payload
        )
    data = (
        await service.views_for_user(user_id=user.str_id, conversations=[updated_conv])
    )[0]
    return ok(request, data=data)


@router.delete(
    "/{message_id}/pin",
    response_model=SuccessResponse[ConversationView],
    responses=build_error_responses(400, 403, 404),
    dependencies=[Depends(rate_limit("30/minute", scope="message_unpin"))],
)
async def unpin_message(
    request: Request,
    sio: Annotated[socketio.AsyncServer, Depends(get_sio)],
    user=Depends(require_verified_user),
    ctx: MessageContext = Depends(require_message_access),
    service: ConversationsService = Depends(get_conversations_service),
):
    conversation = ctx.require_conversation_container()
    updated_conv = await service.unpin_message(
        user_id=user.str_id,
        conversation_id=conversation.str_id,
        message_id=ctx.message.str_id,
    )
    payload = {
        "conversation_id": updated_conv.str_id,
        "pinned_message_ids": updated_conv.pinned_message_ids,
    }
    for participant_id in updated_conv.participant_ids:
        await emit_to_user(
            sio, str(participant_id), "conversation_pins_updated", payload
        )
    data = (
        await service.views_for_user(user_id=user.str_id, conversations=[updated_conv])
    )[0]
    return ok(request, data=data)
