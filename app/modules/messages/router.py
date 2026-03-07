from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from starlette.requests import Request

from app.core.api_models import Meta, SuccessResponse
from app.core.openapi import build_error_responses
from app.core.rate_limit_deps import rate_limit
from app.core.responses import ok
from app.core.security import require_verified_user
from app.db.mongo import get_db
from app.modules.messages.repository import MessagesRepository
from app.modules.messages.schemas import MessageDoc
from app.modules.messages.service import MessagesService
from app.modules.realtime import emit_voice_message_to_receiver

router = APIRouter(
    prefix="/messages",
    tags=["messages"],
    responses=build_error_responses(400, 401, 422, 500),
)


@router.post(
    "/voice",
    status_code=201,
    response_model=SuccessResponse[MessageDoc],
    dependencies=[Depends(rate_limit("30/minute", scope="voice_upload"))],
)
async def upload_voice(
    request: Request,
    receiver_id: str = Form(...),
    duration_ms: Optional[int] = Form(None),
    file: UploadFile = File(...),
    user: dict = Depends(require_verified_user),
):
    db = get_db()
    service = MessagesService(MessagesRepository(db))

    message = await service.upload_voice_message(
        sender_id=str(user["_id"]),
        receiver_id=receiver_id,
        file=file,
        duration_ms=duration_ms,
    )

    await emit_voice_message_to_receiver(
        receiver_id=receiver_id,
        payload=message.model_dump(mode="json"),
    )

    return ok(
        request,
        data=message,
        status_code=201,
    )


@router.get(
    "/{user_id}",
    response_model=SuccessResponse[list[MessageDoc]],
    dependencies=[Depends(rate_limit("30/minute", scope="voice_upload"))],
)
async def history(
    request: Request,
    user_id: str,
    limit: int = Query(20, ge=1, le=100),
    cursor: Optional[str] = Query(None),
    user: dict = Depends(require_verified_user),
):
    db = get_db()
    service = MessagesService(MessagesRepository(db))

    items, next_cursor = await service.get_history(
        user_id=str(user["_id"]),
        peer_user_id=user_id,
        limit=limit,
        cursor=cursor,
    )

    return ok(
        request,
        data=items,
        meta=Meta(
            cursor=cursor,
            next_cursor=next_cursor,
            limit=limit,
        ),
    )