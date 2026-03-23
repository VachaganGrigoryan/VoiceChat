from __future__ import annotations

from typing import Optional, Literal, Annotated

import socketio
from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from starlette.requests import Request

from app.core.deps import get_sio
from app.core.errors.openapi import build_error_responses
from app.core.http import ok, PaginationMeta, SuccessResponse, PaginatedResponse, ok_paginated
from app.core.rate_limit import rate_limit
from app.core.security import require_verified_user
from app.modules.messages.dependencies import get_messages_service
from app.modules.messages.schemas import MessageDoc, SendTextMessageRequest, ConversationItem, EditMessageRequest, \
    DeleteMessageResponse, AddReactionRequest, ThreadSummary, SendStickerMessageRequest
from app.modules.messages.service import MessagesService
from app.modules.realtime import emit_to_user, emit_message_to_receiver, emit_message_status_to_user, \
    emit_message_edited, \
    emit_message_deleted, emit_message_reacted, emit_thread_reply_created, emit_thread_summary_updated

router = APIRouter(
    prefix="/messages",
    tags=["messages"],
    responses=build_error_responses(400, 401, 422, 500),
)


@router.post(
    "/media",
    status_code=201,
    response_model=SuccessResponse[MessageDoc],
    dependencies=[Depends(rate_limit("30/minute", scope="media_upload"))],
)
async def upload_media(
    request: Request,
    sio: Annotated[socketio.AsyncServer, Depends(get_sio)],
    type: Literal["voice", "image", "video"] = Form(...),
    receiver_id: str = Form(...),
    duration_ms: Optional[int] = Form(None),
    text: Optional[str] = Form(None),
        reply_mode: Optional[Literal["quote", "thread"]] = Form(None),
        reply_to_message_id: Optional[str] = Form(None),
    file: UploadFile = File(...),
    user: dict = Depends(require_verified_user),
    service: MessagesService = Depends(get_messages_service),
):
    result = await service.upload_media_message(
        sender_id=str(user["_id"]),
        receiver_id=receiver_id,
        message_type=type,
        file=file,
        text=text,
        duration_ms=duration_ms,
        reply_mode=reply_mode,
        reply_to_message_id=reply_to_message_id,
    )

    if result.thread_summary is not None:
        await emit_thread_reply_created(
            sio,
            sender_id=result.message.sender_id,
            receiver_id=result.message.receiver_id,
            payload=result.message.model_dump(mode="json"),
        )
        await emit_thread_summary_updated(
            sio,
            sender_id=result.message.sender_id,
            receiver_id=result.message.receiver_id,
            payload={
                "thread_root_id": result.thread_summary.thread_root_id,
                "thread_reply_count": result.thread_summary.thread_reply_count,
                "last_thread_reply_at": result.thread_summary.last_thread_reply_at,
            },
        )
    else:
        await emit_message_to_receiver(
            sio,
            receiver_id=receiver_id,
            payload=result.message.model_dump(mode="json"),
        )

    return ok(request, data=result.message, status_code=201)


@router.post(
    "/text",
    status_code=201,
    response_model=SuccessResponse[MessageDoc],
    dependencies=[Depends(rate_limit("60/minute", scope="text_message"))],
)
async def send_text(
    request: Request,
    sio: Annotated[socketio.AsyncServer, Depends(get_sio)],
    body: SendTextMessageRequest,
    user: dict = Depends(require_verified_user),
    service: MessagesService = Depends(get_messages_service),
):
    result = await service.send_text_message(
        sender_id=str(user["_id"]),
        receiver_id=body.receiver_id,
        text=body.text,
        reply_mode=body.reply_mode,
        reply_to_message_id=body.reply_to_message_id,
    )

    if result.thread_summary is not None:
        await emit_thread_reply_created(
            sio,
            sender_id=result.message.sender_id,
            receiver_id=result.message.receiver_id,
            payload=result.message.model_dump(mode="json"),
        )
        await emit_thread_summary_updated(
            sio,
            sender_id=result.message.sender_id,
            receiver_id=result.message.receiver_id,
            payload={
                "thread_root_id": result.thread_summary.thread_root_id,
                "thread_reply_count": result.thread_summary.thread_reply_count,
                "last_thread_reply_at": result.thread_summary.last_thread_reply_at,
            },
        )
    else:
        await emit_message_to_receiver(
            sio,
            receiver_id=body.receiver_id,
            payload=result.message.model_dump(mode="json"),
        )

    return ok(
        request,
        data=result.message,
        status_code=201,
    )


@router.post(
    "/sticker",
    status_code=201,
    response_model=SuccessResponse[MessageDoc],
    dependencies=[Depends(rate_limit("60/minute", scope="sticker_message"))],
)
async def send_sticker(
    request: Request,
    sio: Annotated[socketio.AsyncServer, Depends(get_sio)],
    body: SendStickerMessageRequest,
    user: dict = Depends(require_verified_user),
    service: MessagesService = Depends(get_messages_service),
):
    result = await service.send_sticker_message(
        sender_id=str(user["_id"]),
        receiver_id=body.receiver_id,
        sticker_id=body.sticker_id,
        emoji=body.emoji,
        reply_mode=body.reply_mode,
        reply_to_message_id=body.reply_to_message_id,
    )

    if result.thread_summary is not None:
        await emit_thread_reply_created(
            sio,
            sender_id=result.message.sender_id,
            receiver_id=result.message.receiver_id,
            payload=result.message.model_dump(mode="json"),
        )
        await emit_thread_summary_updated(
            sio,
            sender_id=result.message.sender_id,
            receiver_id=result.message.receiver_id,
            payload={
                "thread_root_id": result.thread_summary.thread_root_id,
                "thread_reply_count": result.thread_summary.thread_reply_count,
                "last_thread_reply_at": result.thread_summary.last_thread_reply_at,
            },
        )
    else:
        await emit_message_to_receiver(
            sio,
            receiver_id=body.receiver_id,
            payload=result.message.model_dump(mode="json"),
        )

    return ok(request, data=result.message, status_code=201)


@router.get(
    "/conversations/{user_id}",
    response_model=PaginatedResponse[list[MessageDoc]],
    dependencies=[Depends(rate_limit("30/minute", scope="message_history"))],
)
async def history(
    request: Request,
    user_id: str,
    limit: int = Query(20, ge=1, le=100),
    cursor: Optional[str] = Query(None),
    user: dict = Depends(require_verified_user),
    service: MessagesService = Depends(get_messages_service),
):
    items, next_cursor = await service.get_history(
        user_id=str(user["_id"]),
        peer_user_id=user_id,
        limit=limit,
        cursor=cursor,
    )

    return ok_paginated(
        request,
        data=items,
        meta=PaginationMeta(
            cursor=cursor,
            next_cursor=next_cursor,
            limit=limit,
        ),
    )


@router.get(
    "/conversations",
    response_model=PaginatedResponse[list[ConversationItem]],
    dependencies=[Depends(rate_limit("30/minute", scope="conversations_list"))],
)
async def conversations(
    request: Request,
    limit: int = Query(50, ge=1, le=100),
    cursor: Optional[str] = Query(None),
    user: dict = Depends(require_verified_user),
    service: MessagesService = Depends(get_messages_service),
):
    items, next_cursor = await service.list_conversations(
        user_id=str(user["_id"]),
        limit=limit,
        cursor=cursor,
    )

    return ok_paginated(
        request,
        data=items,
        meta=PaginationMeta(
            cursor=cursor,
            next_cursor=next_cursor,
            limit=limit,
        ),
    )


@router.post(
    "/conversations/{user_id}/read",
    response_model=SuccessResponse[dict],
)
async def mark_conversation_read(
        request: Request,
        user_id: str,
        user: dict = Depends(require_verified_user),
        service: MessagesService = Depends(get_messages_service),
):
    updated = await service.mark_conversation_read(
        receiver_id=str(user["_id"]),
        peer_user_id=user_id,
    )
    return ok(request, data={"updated_count": updated})


@router.get(
    "/{message_id}/thread",
    response_model=SuccessResponse[list[MessageDoc]],
)
async def get_thread(
        request: Request,
        message_id: str,
        user: dict = Depends(require_verified_user),
        service: MessagesService = Depends(get_messages_service),
):
    items = await service.get_thread(
        message_id=message_id,
        user_id=str(user["_id"]),
    )
    return ok(request, data=items)


@router.get(
    "/{message_id}/thread-summary",
    response_model=SuccessResponse[ThreadSummary],
)
async def get_thread_summary(
        request: Request,
        message_id: str,
        user: dict = Depends(require_verified_user),
        service: MessagesService = Depends(get_messages_service),
):
    summary = await service.get_thread_summary(
        message_id=message_id,
        user_id=str(user["_id"]),
    )
    return ok(request, data=summary)


@router.post(
    "/{message_id}/reactions",
    response_model=SuccessResponse[MessageDoc],
)
async def add_reaction(
        request: Request,
        message_id: str,
        body: AddReactionRequest,
        sio: Annotated[socketio.AsyncServer, Depends(get_sio)],
        user: dict = Depends(require_verified_user),
        service: MessagesService = Depends(get_messages_service),
):
    message = await service.add_reaction(
        message_id=message_id,
        user_id=str(user["_id"]),
        emoji=body.emoji,
    )
    await emit_message_reacted(
        sio,
        sender_id=message.sender_id,
        receiver_id=message.receiver_id,
        payload={
            "message_id": message.id,
            "conversation_id": message.conversation_id,
            "reactions": [reaction.model_dump(mode="json") for reaction in message.reactions],
            "updated_at": message.updated_at,
        },
    )
    return ok(request, data=message)


@router.delete(
    "/{message_id}/reactions/{emoji}/me",
    response_model=SuccessResponse[MessageDoc],
)
async def remove_reaction(
        request: Request,
        message_id: str,
        emoji: str,
        sio: Annotated[socketio.AsyncServer, Depends(get_sio)],
        user: dict = Depends(require_verified_user),
        service: MessagesService = Depends(get_messages_service),
):
    message = await service.remove_reaction(
        message_id=message_id,
        user_id=str(user["_id"]),
        emoji=emoji,
    )
    await emit_message_reacted(
        sio,
        sender_id=message.sender_id,
        receiver_id=message.receiver_id,
        payload={
            "message_id": message.id,
            "conversation_id": message.conversation_id,
            "reactions": [reaction.model_dump(mode="json") for reaction in message.reactions],
            "updated_at": message.updated_at,
        },
    )
    return ok(request, data=message)


@router.post(
    "/{message_id}/delivered",
    response_model=SuccessResponse[MessageDoc],
)
async def mark_delivered(
        request: Request,
        message_id: str,
        sio: Annotated[socketio.AsyncServer, Depends(get_sio)],
        user: dict = Depends(require_verified_user),
        service: MessagesService = Depends(get_messages_service),
):
    message = await service.mark_delivered(
        message_id=message_id,
        receiver_id=str(user["_id"]),
    )
    await emit_message_status_to_user(
        sio,
        user_id=message.sender_id,
        payload={
            "message_id": message.id,
            "status": "delivered",
            "receiver_id": message.receiver_id,
            "delivered_at": message.delivered_at,
        },
    )
    return ok(request, data=message)


@router.post(
    "/{message_id}/read",
    response_model=SuccessResponse[MessageDoc],
)
async def mark_read(
        request: Request,
        message_id: str,
        sio: Annotated[socketio.AsyncServer, Depends(get_sio)],
        user: dict = Depends(require_verified_user),
        service: MessagesService = Depends(get_messages_service),
):
    message = await service.mark_read(
        message_id=message_id,
        receiver_id=str(user["_id"]),
    )
    await emit_message_status_to_user(
        sio,
        user_id=message.sender_id,
        payload={
            "message_id": message.id,
            "status": "read",
            "receiver_id": message.receiver_id,
            "read_at": message.read_at,
            "delivered_at": message.delivered_at,
        },
    )
    return ok(request, data=message)


@router.patch(
    "/{message_id}",
    response_model=SuccessResponse[MessageDoc],
)
async def edit_message(
        request: Request,
        message_id: str,
        body: EditMessageRequest,
        sio: Annotated[socketio.AsyncServer, Depends(get_sio)],
        user: dict = Depends(require_verified_user),
        service: MessagesService = Depends(get_messages_service),
):
    message = await service.edit_text_message(
        message_id=message_id,
        sender_id=str(user["_id"]),
        text=body.text,
    )
    await emit_message_edited(
        sio,
        sender_id=message.sender_id,
        receiver_id=message.receiver_id,
        payload=message.model_dump(mode="json"),
    )
    return ok(request, data=message)


@router.delete(
    "/{message_id}",
    response_model=SuccessResponse[DeleteMessageResponse],
)
async def delete_message(
        request: Request,
        message_id: str,
        sio: Annotated[socketio.AsyncServer, Depends(get_sio)],
        user: dict = Depends(require_verified_user),
        service: MessagesService = Depends(get_messages_service),
):
    outcome = await service.delete_message(
        message_id=message_id,
        actor_user_id=str(user["_id"]),
    )

    payload = outcome.response.model_dump(mode="json")
    if outcome.response.deleted_for_everyone:
        await emit_message_deleted(
            sio,
            sender_id=outcome.sender_id,
            receiver_id=outcome.receiver_id,
            payload=payload,
        )
    else:
        await emit_to_user(
            sio,
            user_id=str(user["_id"]),
            event="message_deleted",
            payload=payload,
        )

    return ok(request, data=outcome.response)
