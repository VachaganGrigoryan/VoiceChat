from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from starlette.requests import Request

from app.core.errors.openapi import build_error_responses
from app.core.http import PaginatedResponse, ok_paginated
from app.core.http.api_models import PaginationMeta
from app.core.rate_limit import rate_limit
from app.core.security import require_verified_user
from app.modules.messages.dependencies import get_messages_service
from app.modules.messages.schemas import MessageDoc
from app.modules.messages.service import MessagesService

router = APIRouter(
    prefix="/search",
    tags=["search"],
    responses=build_error_responses(400, 401, 422, 500),
)


@router.get(
    "/messages",
    response_model=PaginatedResponse[MessageDoc],
    dependencies=[Depends(rate_limit("30/minute", scope="message_search"))],
)
async def search_messages(
    request: Request,
    q: str = Query(..., min_length=1, max_length=200),
    limit: int = Query(20, ge=1, le=100),
    cursor: str | None = Query(default=None),
    user=Depends(require_verified_user),
    service: MessagesService = Depends(get_messages_service),
):
    items, next_cursor = await service.search_messages(
        user_id=user.str_id, query=q, limit=limit, cursor=cursor
    )
    return ok_paginated(
        request,
        data=items,
        meta=PaginationMeta(cursor=cursor, next_cursor=next_cursor, limit=limit),
    )
