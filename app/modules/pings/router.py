from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request

from app.core.security import get_current_user_id
from app.modules.pings.schemas import PingListResponse, PingResponse, SendPingRequest

router = APIRouter(prefix="/pings", tags=["pings"])


def get_pings_service(request: Request):
    return request.app.state.pings_service


@router.post("", response_model=PingResponse)
async def send_ping(
    body: SendPingRequest,
    request: Request,
    user_id=Depends(get_current_user_id),
):
    service = get_pings_service(request)
    return await service.send_ping(from_user_id=user_id, to_user_id=body.to_user_id)


@router.get("/incoming", response_model=PingListResponse)
async def list_incoming(
    request: Request,
    user_id=Depends(get_current_user_id),
    limit: int = Query(default=20, ge=1, le=100),
):
    service = get_pings_service(request)
    return await service.list_incoming(user_id=user_id, limit=limit)


@router.get("/outgoing", response_model=PingListResponse)
async def list_outgoing(
    request: Request,
    user_id=Depends(get_current_user_id),
    limit: int = Query(default=20, ge=1, le=100),
):
    service = get_pings_service(request)
    return await service.list_outgoing(user_id=user_id, limit=limit)


@router.post("/{ping_id}/accept", response_model=PingResponse)
async def accept_ping(
    ping_id: str,
    request: Request,
    user_id=Depends(get_current_user_id),
):
    service = get_pings_service(request)
    return await service.accept_ping(user_id=user_id, ping_id=ping_id)


@router.post("/{ping_id}/decline", response_model=PingResponse)
async def decline_ping(
    ping_id: str,
    request: Request,
    user_id=Depends(get_current_user_id),
):
    service = get_pings_service(request)
    return await service.decline_ping(user_id=user_id, ping_id=ping_id)