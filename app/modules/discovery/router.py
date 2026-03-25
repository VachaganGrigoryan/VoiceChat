from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request

from app.core.errors.openapi import build_error_responses
from app.core.http import SuccessResponse, ok
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

router = APIRouter(
    prefix="/discovery",
    tags=["discovery"],
    responses=build_error_responses(400, 401, 404, 422, 500),
)


@router.post("/code/regenerate", response_model=SuccessResponse[RegenerateCodeResponse])
async def regenerate_code(
    request: Request,
    user_id=Depends(get_current_user_id),
    service: DiscoveryService = Depends(get_discovery_service),
):
    return ok(request, data=await service.regenerate_code(user_id=user_id))


@router.post("/code/resolve", response_model=SuccessResponse[DiscoveryUserSummary])
async def resolve_code(
    request: Request,
    body: ResolveCodeRequest,
    user_id=Depends(get_current_user_id),
    service: DiscoveryService = Depends(get_discovery_service),
):
    return ok(
        request,
        data=await service.resolve_code(code=body.code, requester_user_id=user_id),
    )


@router.post("/links", response_model=SuccessResponse[CreateInviteLinkResponse])
async def create_link(
    request: Request,
    body: CreateInviteLinkRequest,
    user_id=Depends(get_current_user_id),
    service: DiscoveryService = Depends(get_discovery_service),
):
    return ok(
        request,
        data=await service.create_link(
            user_id=user_id,
            expires_in_seconds=body.expires_in_seconds,
            max_uses=body.max_uses,
        ),
    )


@router.get("/invite/{token}", response_model=SuccessResponse[DiscoveryUserSummary])
async def resolve_link(
    request: Request,
    token: str,
    user_id=Depends(get_current_user_id),
    service: DiscoveryService = Depends(get_discovery_service),
):
    return ok(
        request, data=await service.resolve_link(token=token, requester_user_id=user_id)
    )


@router.get("/users/search", response_model=SuccessResponse[list[DiscoveryUserSummary]])
async def search_users(
    request: Request,
    q: str = Query(min_length=1, max_length=64),
    limit: int = Query(default=20, ge=1, le=50),
    user_id=Depends(get_current_user_id),
    service: DiscoveryService = Depends(get_discovery_service),
):
    return ok(
        request,
        data=await service.search_users(q=q, requester_user_id=user_id, limit=limit),
    )
