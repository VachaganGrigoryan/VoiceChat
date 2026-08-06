from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query
from starlette.requests import Request

from app.core.errors.openapi import build_error_responses
from app.core.http import PaginatedResponse, PaginationMeta, ok_paginated
from app.core.rate_limit import rate_limit
from app.core.security import require_verified_user
from app.modules.feeds.dependencies import get_feeds_service
from app.modules.feeds.schemas import FeedPostView
from app.modules.feeds.service import FeedService

router = APIRouter(
    prefix="/feeds",
    tags=["feeds"],
    responses=build_error_responses(400, 401, 403, 404, 422, 500),
)


@router.get(
    "",
    response_model=PaginatedResponse[list[FeedPostView]],
    dependencies=[Depends(rate_limit("30/minute", scope="feed_home"))],
)
async def list_home_feed(
    request: Request,
    limit: int = Query(20, ge=1, le=100),
    cursor: Optional[str] = Query(None),
    user=Depends(require_verified_user),
    service: FeedService = Depends(get_feeds_service),
):
    items, next_cursor = await service.home(
        user_id=user.str_id,
        limit=limit,
        cursor=cursor,
    )
    return ok_paginated(
        request,
        data=items,
        meta=PaginationMeta(cursor=cursor, next_cursor=next_cursor, limit=limit),
    )


@router.get(
    "/channels/{channel_id}",
    response_model=PaginatedResponse[list[FeedPostView]],
    dependencies=[Depends(rate_limit("30/minute", scope="feed_channel"))],
)
async def list_channel_feed(
    request: Request,
    channel_id: str,
    limit: int = Query(20, ge=1, le=100),
    cursor: Optional[str] = Query(None),
    user=Depends(require_verified_user),
    service: FeedService = Depends(get_feeds_service),
):
    items, next_cursor = await service.list_channel_posts(
        viewer_id=user.str_id,
        channel_id=channel_id,
        limit=limit,
        cursor=cursor,
    )
    return ok_paginated(
        request,
        data=items,
        meta=PaginationMeta(cursor=cursor, next_cursor=next_cursor, limit=limit),
    )


@router.get(
    "/channels/{channel_id}/posts/{post_id}/comments",
    response_model=PaginatedResponse[list[FeedPostView]],
    dependencies=[Depends(rate_limit("30/minute", scope="feed_post_comments"))],
)
async def list_post_comments(
    request: Request,
    channel_id: str,
    post_id: str,
    user=Depends(require_verified_user),
    service: FeedService = Depends(get_feeds_service),
):
    items = await service.list_channel_post_comments(
        viewer_id=user.str_id, channel_id=channel_id, post_id=post_id
    )
    return ok_paginated(
        request,
        data=items,
        meta=PaginationMeta(cursor=None, next_cursor=None, limit=len(items)),
    )


@router.get(
    "/users/{username}",
    response_model=PaginatedResponse[list[FeedPostView]],
    dependencies=[Depends(rate_limit("30/minute", scope="feed_profile"))],
)
async def list_profile_feed(
    request: Request,
    username: str,
    limit: int = Query(20, ge=1, le=100),
    cursor: Optional[str] = Query(None),
    user=Depends(require_verified_user),
    service: FeedService = Depends(get_feeds_service),
):
    items, next_cursor = await service.list_profile_posts(
        viewer_id=user.str_id,
        username=username,
        limit=limit,
        cursor=cursor,
    )
    return ok_paginated(
        request,
        data=items,
        meta=PaginationMeta(cursor=cursor, next_cursor=next_cursor, limit=limit),
    )
