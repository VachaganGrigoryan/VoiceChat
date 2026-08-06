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
from app.modules.conversations.schemas import ConversationSendTextRequest
from app.modules.conversations.service import ConversationsService
from app.modules.channels.dependencies import get_channel_service
from app.modules.channels.service import ChannelService
from app.modules.messages.dependencies import (
    ContainerContext,
    MessageContext,
    get_messages_service,
    require_container_manage,
    require_container_post,
    require_container_read,
    require_message_access,
)
from app.modules.messages.emit_helpers import (
    emit_message_notifications,
    emit_send_result,
    emit_to_container,
    fan_out_container_send,
)
from app.modules.messages.schemas import (
    AddReactionRequest,
    ClearChatResponse,
    DeleteMessageResponse,
    EditMessageRequest,
    ForwardMessageRequest,
    MessageDoc,
    PinnedMessagesView,
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

#: Collection operations addressed by container, serving both container types.
#: Shares the ``/messages`` prefix with the item router above, and its routes
#: have the same segment count as the item routes, so **it must be registered
#: after `router`** or `DELETE /messages/{id}/pin` matches
#: `DELETE /messages/{container_type}/{container_id}` first and fails
#: validation on an id that is not a container type. See `app/routes.py`.
container_messages_router = APIRouter(
    prefix="/messages",
    tags=["messages"],
    responses=build_error_responses(400, 401, 403, 404, 422, 500),
)


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
    channels_service: ChannelService = Depends(get_channel_service),
    rel_service: RelationshipService = Depends(get_relationship_service),
):
    # Both container types keep a per-viewer read cursor; they keep it in
    # different places, which is the whole of the difference here.
    if ctx.container_type == "conversation":
        await conversations_service.mark_conversation_read(
            user_id=user.str_id,
            conversation_id=ctx.container_id,
            last_read_message_id=ctx.message.str_id,
        )
    else:
        await channels_service.advance_read_cursor(
            channel_id=ctx.container_id,
            user_id=user.str_id,
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


@router.delete(
    "/{message_id}/scheduled",
    status_code=204,
    responses=build_error_responses(403, 404),
    dependencies=[Depends(rate_limit("30/minute", scope="scheduled_message_cancel"))],
)
async def cancel_scheduled_message_by_id(
    request: Request,
    user=Depends(require_verified_user),
    ctx: MessageContext = Depends(require_message_access),
    messages: MessagesService = Depends(get_messages_service),
):
    """Cancel a scheduled message, addressed like any other message.

    The scheduled message names its own container, so no container segment is
    needed. The repository's sender filter is what keeps cancellation to the
    author -- a reader of the container gets a 404, not someone else's message.
    """
    await messages.cancel_scheduled_message(
        container_type=ctx.container_type,
        container_id=ctx.container_id,
        message_id=ctx.message.str_id,
        sender_id=user.str_id,
    )
    return ok(request, data=None, status_code=204)


async def _pinned_view(
    sio: socketio.AsyncServer,
    *,
    ctx: MessageContext,
    pinned_message_ids: list[str],
    rel_service: RelationshipService,
) -> PinnedMessagesView:
    """Announce a container's new pinned set and return the view of it.

    A channel reaches its audience through its room; a conversation reaches its
    participants one at a time. `emit_to_container` already knows the
    difference, so pinning no longer fans out over `participant_ids` by hand.
    """
    view = PinnedMessagesView(
        container_type=ctx.container_type,
        container_id=ctx.container_id,
        pinned_message_ids=[str(message_id) for message_id in pinned_message_ids],
    )
    await emit_to_container(
        sio,
        container_type=ctx.container_type,
        container_id=ctx.container_id,
        conversation=ctx.conversation,
        relationships_service=rel_service,
        event="conversation_pins_updated",
        payload=view.model_dump(mode="json"),
    )
    return view


@router.post(
    "/{message_id}/pin",
    response_model=SuccessResponse[PinnedMessagesView],
    responses=build_error_responses(400, 403, 404),
    dependencies=[Depends(rate_limit("30/minute", scope="message_pin"))],
)
async def pin_message(
    request: Request,
    sio: Annotated[socketio.AsyncServer, Depends(get_sio)],
    user=Depends(require_verified_user),
    ctx: MessageContext = Depends(require_message_access),
    service: ConversationsService = Depends(get_conversations_service),
    channels: ChannelService = Depends(get_channel_service),
    rel_service: RelationshipService = Depends(get_relationship_service),
):
    if ctx.container_type == "channel":
        updated = await channels.pin_message(
            channel_id=ctx.container_id,
            message_id=ctx.message.str_id,
            actor_user_id=user.str_id,
        )
    else:
        updated = await service.pin_message(
            user_id=user.str_id,
            conversation_id=ctx.container_id,
            message_id=ctx.message.str_id,
        )
    data = await _pinned_view(
        sio,
        ctx=ctx,
        pinned_message_ids=updated.pinned_message_ids,
        rel_service=rel_service,
    )
    return ok(request, data=data)


@router.delete(
    "/{message_id}/pin",
    response_model=SuccessResponse[PinnedMessagesView],
    responses=build_error_responses(400, 403, 404),
    dependencies=[Depends(rate_limit("30/minute", scope="message_unpin"))],
)
async def unpin_message(
    request: Request,
    sio: Annotated[socketio.AsyncServer, Depends(get_sio)],
    user=Depends(require_verified_user),
    ctx: MessageContext = Depends(require_message_access),
    service: ConversationsService = Depends(get_conversations_service),
    channels: ChannelService = Depends(get_channel_service),
    rel_service: RelationshipService = Depends(get_relationship_service),
):
    if ctx.container_type == "channel":
        updated = await channels.unpin_message(
            channel_id=ctx.container_id,
            message_id=ctx.message.str_id,
            actor_user_id=user.str_id,
        )
    else:
        updated = await service.unpin_message(
            user_id=user.str_id,
            conversation_id=ctx.container_id,
            message_id=ctx.message.str_id,
        )
    data = await _pinned_view(
        sio,
        ctx=ctx,
        pinned_message_ids=updated.pinned_message_ids,
        rel_service=rel_service,
    )
    return ok(request, data=data)


# ============================================================================
# Collection Operations, addressed by container
# (/messages/{container_type}/{container_id}...)
#
# One family for both container types. The handlers stay thin because the
# service layer is already container-generic: it takes the discriminator and
# resolves permissions against the container resource, so nothing below here
# branches on which kind of container it was handed.
# ============================================================================


async def _after_conversation_send(
    service: ConversationsService,
    *,
    ctx: ContainerContext,
    user_id: str,
    clear_draft: bool = True,
) -> None:
    """Run the inbox bookkeeping a send triggers, where there is an inbox.

    Drafts and resurfacing are properties of a conversation's inbox row; a
    channel has neither, so for a channel this does nothing rather than
    pretending it has an equivalent.
    """
    if ctx.container_type != "conversation":
        return
    if clear_draft:
        await service.clear_draft(user_id=user_id, conversation_id=ctx.container_id)
    await service.resurface_on_send(user_id=user_id, conversation_id=ctx.container_id)


@container_messages_router.get(
    "/{container_type}/{container_id}",
    response_model=PaginatedResponse[list[MessageDoc]],
    dependencies=[Depends(rate_limit("60/minute", scope="container_messages"))],
)
async def list_container_messages(
    request: Request,
    limit: int = Query(20, ge=1, le=100),
    cursor: Optional[str] = Query(None),
    user=Depends(require_verified_user),
    ctx: ContainerContext = Depends(require_container_read),
    messages: MessagesService = Depends(get_messages_service),
):
    items, next_cursor = await messages.get_history(
        user_id=user.str_id,
        container_type=ctx.container_type,
        container_id=ctx.container_id,
        limit=limit,
        cursor=cursor,
    )
    return ok_paginated(
        request,
        data=items,
        meta=PaginationMeta(cursor=cursor, next_cursor=next_cursor, limit=limit),
    )


@container_messages_router.post(
    "/{container_type}/{container_id}/text",
    status_code=201,
    response_model=SuccessResponse[MessageDoc],
    dependencies=[Depends(rate_limit("60/minute", scope="container_message_text"))],
)
async def send_container_text(
    request: Request,
    body: ConversationSendTextRequest,
    sio: Annotated[socketio.AsyncServer, Depends(get_sio)],
    user=Depends(require_verified_user),
    ctx: ContainerContext = Depends(require_container_post),
    service: ConversationsService = Depends(get_conversations_service),
    messages: MessagesService = Depends(get_messages_service),
    notifications: NotificationsService = Depends(get_notifications_service),
):
    result = await messages.send_text(
        sender_id=user.str_id,
        container_type=ctx.container_type,
        container_id=ctx.container_id,
        text=body.text,
        reply_mode=body.reply_mode,
        reply_to_message_id=body.reply_to_message_id,
        style=(TextStyleDocument(**body.style.model_dump()) if body.style else None),
    )
    await fan_out_container_send(
        sio,
        container_type=ctx.container_type,
        container_id=ctx.container_id,
        result=result,
        notifications=notifications,
        conversation=ctx.conversation,
    )
    await _after_conversation_send(service, ctx=ctx, user_id=user.str_id)
    return ok(request, data=result.message, status_code=201)


@container_messages_router.post(
    "/{container_type}/{container_id}/media",
    status_code=201,
    response_model=SuccessResponse[MessageDoc],
    dependencies=[Depends(rate_limit("30/minute", scope="container_message_media"))],
)
async def send_container_media(
    request: Request,
    sio: Annotated[socketio.AsyncServer, Depends(get_sio)],
    type: Literal["media", "file"] = Form(...),
    media_kind: Optional[Literal["voice", "audio", "image", "video"]] = Form(None),
    duration_ms: Optional[int] = Form(None),
    text: Optional[str] = Form(None),
    reply_mode: Optional[Literal["quote", "thread"]] = Form(None),
    reply_to_message_id: Optional[str] = Form(None),
    file: UploadFile = File(...),
    user=Depends(require_verified_user),
    ctx: ContainerContext = Depends(require_container_post),
    service: ConversationsService = Depends(get_conversations_service),
    messages: MessagesService = Depends(get_messages_service),
    notifications: NotificationsService = Depends(get_notifications_service),
):
    result = await messages.upload_media(
        sender_id=user.str_id,
        container_type=ctx.container_type,
        container_id=ctx.container_id,
        message_type=type,
        media_kind=media_kind,
        file=file,
        text=text,
        duration_ms=duration_ms,
        reply_mode=reply_mode,
        reply_to_message_id=reply_to_message_id,
    )
    await fan_out_container_send(
        sio,
        container_type=ctx.container_type,
        container_id=ctx.container_id,
        result=result,
        notifications=notifications,
        conversation=ctx.conversation,
    )
    # A media send leaves the draft alone: the draft holds unsent text, which
    # this did not send.
    await _after_conversation_send(
        service, ctx=ctx, user_id=user.str_id, clear_draft=False
    )
    return ok(request, data=result.message, status_code=201)


@container_messages_router.post(
    "/{container_type}/{container_id}/content",
    status_code=201,
    response_model=SuccessResponse[MessageDoc],
    dependencies=[Depends(rate_limit("60/minute", scope="container_message_content"))],
)
async def send_container_rich_content(
    request: Request,
    body: SendRichContentRequest,
    sio: Annotated[socketio.AsyncServer, Depends(get_sio)],
    user=Depends(require_verified_user),
    ctx: ContainerContext = Depends(require_container_post),
    service: ConversationsService = Depends(get_conversations_service),
    messages: MessagesService = Depends(get_messages_service),
    notifications: NotificationsService = Depends(get_notifications_service),
):
    result = await messages.send_rich_content(
        sender_id=user.str_id,
        container_type=ctx.container_type,
        container_id=ctx.container_id,
        body=body,
    )
    await fan_out_container_send(
        sio,
        container_type=ctx.container_type,
        container_id=ctx.container_id,
        result=result,
        notifications=notifications,
        conversation=ctx.conversation,
    )
    await _after_conversation_send(service, ctx=ctx, user_id=user.str_id)
    return ok(request, data=result.message, status_code=201)


@container_messages_router.delete(
    "/{container_type}/{container_id}",
    response_model=SuccessResponse[ClearChatResponse],
    dependencies=[Depends(rate_limit("30/minute", scope="container_clear"))],
)
async def clear_container_messages(
    request: Request,
    user=Depends(require_verified_user),
    ctx: ContainerContext = Depends(require_container_read),
    messages: MessagesService = Depends(get_messages_service),
):
    """Clear the caller's own view of a container.

    Per-viewer in both container types: the caller's own messages go, everyone
    else's are marked hidden for them alone. Nobody else's view changes, which
    is why reading the container is right to ask for.
    """
    container_id, count = await messages.clear_chat_history(
        container_type=ctx.container_type,
        container_id=ctx.container_id,
        user_id=user.str_id,
    )
    return ok(
        request,
        data=ClearChatResponse(conversation_id=container_id, cleared_count=count),
    )


@container_messages_router.delete(
    "/{container_type}/{container_id}/all",
    response_model=SuccessResponse[ClearChatResponse],
    dependencies=[Depends(rate_limit("10/minute", scope="container_clear_all"))],
)
async def clear_container_messages_for_everyone(
    request: Request,
    sio: Annotated[socketio.AsyncServer, Depends(get_sio)],
    ctx: ContainerContext = Depends(require_container_manage),
    messages: MessagesService = Depends(get_messages_service),
    rel_service: RelationshipService = Depends(get_relationship_service),
):
    container_id, count = await messages.clear_chat_history_for_everyone(
        container_type=ctx.container_type,
        container_id=ctx.container_id,
    )
    await emit_to_container(
        sio,
        container_type=ctx.container_type,
        container_id=ctx.container_id,
        conversation=ctx.conversation,
        relationships_service=rel_service,
        event="conversation_history_cleared",
        payload={"conversation_id": container_id, "cleared_count": count},
    )
    return ok(
        request,
        data=ClearChatResponse(conversation_id=container_id, cleared_count=count),
    )


@container_messages_router.post(
    "/{container_type}/{container_id}/schedule",
    status_code=201,
    response_model=SuccessResponse[MessageDoc],
    dependencies=[Depends(rate_limit("30/minute", scope="container_schedule"))],
)
async def schedule_container_message(
    request: Request,
    body: ScheduleMessageRequest,
    user=Depends(require_verified_user),
    ctx: ContainerContext = Depends(require_container_post),
    messages: MessagesService = Depends(get_messages_service),
):
    message = await messages.schedule_message(
        container_type=ctx.container_type,
        container_id=ctx.container_id,
        sender_id=user.str_id,
        text=body.text,
        scheduled_for=body.scheduled_for,
    )
    return ok(request, data=message, status_code=201)


@container_messages_router.get(
    "/{container_type}/{container_id}/scheduled",
    response_model=SuccessResponse[list[MessageDoc]],
    dependencies=[Depends(rate_limit("30/minute", scope="container_scheduled"))],
)
async def list_container_scheduled_messages(
    request: Request,
    user=Depends(require_verified_user),
    ctx: ContainerContext = Depends(require_container_read),
    messages: MessagesService = Depends(get_messages_service),
):
    items = await messages.list_scheduled_messages(
        container_type=ctx.container_type,
        container_id=ctx.container_id,
        sender_id=user.str_id,
    )
    return ok(request, data=items)


@container_messages_router.get(
    "/{container_type}/{container_id}/pinned",
    response_model=SuccessResponse[list[MessageDoc]],
    dependencies=[Depends(rate_limit("30/minute", scope="container_pinned"))],
)
async def list_container_pinned_messages(
    request: Request,
    user=Depends(require_verified_user),
    ctx: ContainerContext = Depends(require_container_read),
    messages: MessagesService = Depends(get_messages_service),
):
    """The container's pinned messages.

    Both kinds track pinned ids on the container document itself, so the gate
    that resolved the container already carries them -- and the channel's read
    policy, rather than its membership, is what decided the caller may see them.
    """
    items = await messages.get_messages_by_ids(
        container_type=ctx.container_type,
        container_id=ctx.container_id,
        message_ids=ctx.pinned_message_ids,
        user_id=user.str_id,
    )
    return ok(request, data=items)
