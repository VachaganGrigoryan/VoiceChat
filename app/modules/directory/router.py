from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request

from app.core.errors.openapi import build_error_responses
from app.core.http import PaginatedResponse, SuccessResponse, ok, ok_paginated
from app.core.http.api_models import PaginationMeta
from app.core.security import get_current_user_id
from app.modules.channels.schemas import ChannelSummary
from app.modules.directory.schemas import (
    DirectorySort,
    GroupSummary,
    OmniResults,
    SpaceSummary,
)
from app.modules.directory.service import DirectoryService
from app.modules.discovery.dependencies import get_discovery_service
from app.modules.discovery.schemas import DiscoveryUserSummary
from app.modules.discovery.service import DiscoveryService

router = APIRouter(
    prefix="/directory",
    tags=["directory"],
    responses=build_error_responses(400, 401, 403, 404, 422, 500),
)


def get_directory_service() -> DirectoryService:
    return DirectoryService()


def _meta(limit: int, cursor: str | None, next_cursor: str | None) -> PaginationMeta:
    # `total` stays null: counting a policy-filtered directory on every page
    # would cost a second full scan, and a fabricated number is worse than none.
    return PaginationMeta(
        cursor=cursor, next_cursor=next_cursor, limit=limit, total=None
    )


@router.get("/channels", response_model=PaginatedResponse[ChannelSummary])
async def list_channels(
    request: Request,
    q: str | None = Query(default=None, max_length=200),
    owner_type: str | None = Query(default=None, pattern="^(user|space)$"),
    space_id: str | None = Query(default=None),
    kind: str | None = Query(default=None, pattern="^(text|announcement|profile)$"),
    tags: list[str] | None = Query(default=None),
    sort: DirectorySort = Query(default="relevance"),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=50),
    user_id=Depends(get_current_user_id),
    service: DirectoryService = Depends(get_directory_service),
):
    items, next_cursor = await service.list_channels(
        viewer_id=user_id,
        q=q,
        owner_type=owner_type,
        space_id=space_id,
        kind=kind,
        tags=tags,
        sort=sort,
        cursor=cursor,
        limit=limit,
    )
    return ok_paginated(request, items, meta=_meta(limit, cursor, next_cursor))


@router.get("/spaces", response_model=PaginatedResponse[SpaceSummary])
async def list_spaces(
    request: Request,
    q: str | None = Query(default=None, max_length=200),
    join_policy: str | None = Query(
        default=None, pattern="^(open|approval|invite_only|closed)$"
    ),
    sort: DirectorySort = Query(default="relevance"),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=50),
    user_id=Depends(get_current_user_id),
    service: DirectoryService = Depends(get_directory_service),
):
    items, next_cursor = await service.list_spaces(
        viewer_id=user_id, q=q, join_policy=join_policy, cursor=cursor, limit=limit
    )
    return ok_paginated(request, items, meta=_meta(limit, cursor, next_cursor))


@router.get("/groups", response_model=PaginatedResponse[GroupSummary])
async def list_groups(
    request: Request,
    q: str | None = Query(default=None, max_length=200),
    space_id: str | None = Query(default=None),
    sort: DirectorySort = Query(default="relevance"),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=50),
    user_id=Depends(get_current_user_id),
    service: DirectoryService = Depends(get_directory_service),
):
    items, next_cursor = await service.list_groups(
        viewer_id=user_id, q=q, space_id=space_id, cursor=cursor, limit=limit
    )
    return ok_paginated(request, items, meta=_meta(limit, cursor, next_cursor))


@router.get("/people", response_model=PaginatedResponse[DiscoveryUserSummary])
async def list_people(
    request: Request,
    q: str = Query(min_length=1, max_length=64),
    limit: int = Query(default=20, ge=1, le=50),
    user_id=Depends(get_current_user_id),
    discovery: DiscoveryService = Depends(get_discovery_service),
):
    """A thin alias over discovery's user search.

    Deliberately not a reimplementation: profile privacy and block filtering
    must have exactly one implementation, and it already lives there. The alias
    exists so a client has one directory client and one envelope for all four
    Discover tabs.
    """
    users = await discovery.search_users(q=q, requester_user_id=user_id, limit=limit)
    return ok_paginated(request, users, meta=_meta(limit, None, None))


@router.get("", response_model=SuccessResponse[OmniResults])
async def omni(
    request: Request,
    q: str = Query(min_length=1, max_length=200),
    types: str | None = Query(default=None),
    user_id=Depends(get_current_user_id),
    service: DirectoryService = Depends(get_directory_service),
    discovery: DiscoveryService = Depends(get_discovery_service),
):
    """A bounded preview across entity types.

    Explicitly unpaginated — the typed endpoints are the paginated surface, and
    "see all" links there. A cursor here would imply a stable ordering across
    heterogeneous types that does not exist.
    """
    wanted = (
        [item.strip() for item in types.split(",") if item.strip()] if types else None
    )
    results = await service.omni(viewer_id=user_id, q=q, types=wanted)
    if wanted is None or "people" in wanted:
        results.people = await discovery.search_users(
            q=q, requester_user_id=user_id, limit=5
        )
    return ok(request, data=results)


__all__ = ["router"]
