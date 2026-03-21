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
    DeleteMessageResponse
from app.modules.messages.service import MessagesService
from app.modules.realtime import emit_to_user, emit_message_to_receiver, emit_message_status_to_user, emit_message_edited, \
    emit_message_deleted

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
    type: Literal["voice", "image", "sticker", "video"] = Form(...),
    receiver_id: str = Form(...),
    duration_ms: Optional[int] = Form(None),
    text: Optional[str] = Form(None),
    file: UploadFile = File(...),
    user: dict = Depends(require_verified_user),
    service: MessagesService = Depends(get_messages_service),
):
    message = await service.upload_media_message(
        sender_id=str(user["_id"]),
        receiver_id=receiver_id,
        message_type=type,
        file=file,
        text=text,
        duration_ms=duration_ms,
    )

    await emit_message_to_receiver(
        sio,
        receiver_id=receiver_id,
        payload=message.model_dump(mode="json"),
    )

    return ok(request, data=message, status_code=201)


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
    message = await service.send_text_message(
        sender_id=str(user["_id"]),
        receiver_id=body.receiver_id,
        text=body.text,
    )

    await emit_message_to_receiver(
        sio,
        receiver_id=body.receiver_id,
        payload=message.model_dump(mode="json"),
    )

    return ok(
        request,
        data=message,
        status_code=201,
    )


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
