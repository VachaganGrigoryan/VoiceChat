from __future__ import annotations

from fastapi import APIRouter, Depends
from starlette.requests import Request

from app.core.errors.openapi import build_error_responses
from app.core.http import SuccessResponse, ok
from app.core.security import require_verified_user
from app.modules.webrtc.dependencies import get_webrtc_service
from app.modules.webrtc.schemas import IceServersPayload
from app.modules.webrtc.service import WebRTCService

router = APIRouter(
    prefix="/webrtc",
    tags=["webrtc"],
    responses=build_error_responses(400, 401, 403, 422, 500),
)


@router.get("/ice-servers", response_model=SuccessResponse[IceServersPayload])
async def get_ice_servers(
    request: Request,
    _user: dict = Depends(require_verified_user),
    service: WebRTCService = Depends(get_webrtc_service),
):
    payload = IceServersPayload(ice_servers=await service.get_ice_servers())
    return ok(request, data=payload)
