from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query
from starlette.requests import Request

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
from app.modules.channels.dependencies import get_channel_service
from app.modules.channels.schemas import (
    ChannelCreateRequest,
    ChannelMessageCreateRequest,
    ChannelUpdateRequest,
    ChannelView,
)
from app.modules.channels.service import ChannelService
from app.modules.messages.schemas import MessageDoc

router = APIRouter(
    prefix="/channels",
    tags=["channels"],
    responses=build_error_responses(400, 401, 403, 404, 409, 422, 500),
)


@router.post(
    "",
    status_code=201,
    response_model=SuccessResponse[ChannelView],
    dependencies=[Depends(rate_limit("20/minute", scope="channel_create"))],
)
async def create_channel(
    request: Request,
    body: ChannelCreateRequest,
    user=Depends(require_verified_user),
    service: ChannelService = Depends(get_channel_service),
):
    channel = await service.create(
        created_by=user.str_id,
        name=body.name,
        slug=body.slug,
        kind=body.kind,
        description=body.description,
        visibility=body.visibility,
        join_policy=body.join_policy,
        posting_policy=body.posting_policy,
        comment_policy=body.comment_policy,
        tags=body.tags,
    )
    return ok(request, data=channel, status_code=201)


@router.get(
    "/{channel_id}",
    response_model=SuccessResponse[ChannelView],
    dependencies=[Depends(rate_limit("60/minute", scope="channel_get"))],
)
async def get_channel(
    request: Request,
    channel_id: str,
    user=Depends(require_verified_user),
    service: ChannelService = Depends(get_channel_service),
):
    channel = await service.get(channel_id=channel_id, viewer_id=user.str_id)
    return ok(request, data=channel)


@router.patch(
    "/{channel_id}",
    response_model=SuccessResponse[ChannelView],
    dependencies=[Depends(rate_limit("30/minute", scope="channel_update"))],
)
async def update_channel(
    request: Request,
    channel_id: str,
    body: ChannelUpdateRequest,
    user=Depends(require_verified_user),
    service: ChannelService = Depends(get_channel_service),
):
    channel = await service.update(
        channel_id=channel_id,
        actor_user_id=user.str_id,
        updates=body.model_dump(exclude_unset=True),
    )
    return ok(request, data=channel)


@router.get(
    "/{channel_id}/messages",
    response_model=PaginatedResponse[list[MessageDoc]],
    dependencies=[Depends(rate_limit("60/minute", scope="channel_messages"))],
)
async def list_channel_messages(
    request: Request,
    channel_id: str,
    limit: int = Query(default=20, ge=1, le=100),
    cursor: Optional[str] = Query(default=None),
    user=Depends(require_verified_user),
    service: ChannelService = Depends(get_channel_service),
):
    items, next_cursor = await service.list_messages(
        channel_id=channel_id,
        viewer_id=user.str_id,
        limit=limit,
        cursor=cursor,
    )
    return ok_paginated(
        request,
        data=items,
        meta=PaginationMeta(cursor=cursor, next_cursor=next_cursor, limit=limit),
    )


@router.post(
    "/{channel_id}/messages",
    status_code=201,
    response_model=SuccessResponse[MessageDoc],
    dependencies=[Depends(rate_limit("60/minute", scope="channel_message_create"))],
)
async def create_channel_message(
    request: Request,
    channel_id: str,
    body: ChannelMessageCreateRequest,
    user=Depends(require_verified_user),
    service: ChannelService = Depends(get_channel_service),
):
    message = await service.create_message(
        channel_id=channel_id,
        sender_id=user.str_id,
        text=body.text,
        reply_mode=body.reply_mode,
        reply_to_message_id=body.reply_to_message_id,
    )
    return ok(request, data=message, status_code=201)
