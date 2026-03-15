from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request

from app.core.security import get_current_user_id
from app.modules.discovery.dependencies import get_discovery_service
from app.modules.discovery.schemas import (
    CreateInviteLinkRequest,
    CreateInviteLinkResponse,
    DiscoveryUserSummary,
    RegenerateCodeResponse,
    ResolveCodeRequest,
)
from app.modules.discovery.service import DiscoveryService

router = APIRouter(prefix="/discovery", tags=["discovery"])


@router.post("/code/regenerate", response_model=RegenerateCodeResponse)
async def regenerate_code(
    user_id=Depends(get_current_user_id),
    service: DiscoveryService = Depends(get_discovery_service),
):
    return await service.regenerate_code(user_id=user_id)


@router.post("/code/resolve", response_model=DiscoveryUserSummary)
async def resolve_code(
    body: ResolveCodeRequest,
    user_id=Depends(get_current_user_id),
    service: DiscoveryService = Depends(get_discovery_service),
):
    return await service.resolve_code(code=body.code, requester_user_id=user_id)


@router.post("/links", response_model=CreateInviteLinkResponse)
async def create_link(
    body: CreateInviteLinkRequest,
    user_id=Depends(get_current_user_id),
    service: DiscoveryService = Depends(get_discovery_service),
):
    return await service.create_link(
        user_id=user_id,
        expires_in_seconds=body.expires_in_seconds,
        max_uses=body.max_uses,
    )


@router.get("/invite/{token}", response_model=DiscoveryUserSummary)
async def resolve_link(
    token: str,
    user_id=Depends(get_current_user_id),
    service: DiscoveryService = Depends(get_discovery_service),
):
    return await service.resolve_link(token=token, requester_user_id=user_id)


@router.get("/users/search", response_model=list[DiscoveryUserSummary])
async def search_users(
    q: str = Query(min_length=1, max_length=64),
    limit: int = Query(default=20, ge=1, le=50),
    user_id=Depends(get_current_user_id),
    service: DiscoveryService = Depends(get_discovery_service),
):
    return await service.search_users(q=q, requester_user_id=user_id, limit=limit)