from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from starlette.requests import Request

from app.core.api_models import SuccessResponse
from app.core.openapi import build_error_responses
from app.core.responses import ok
from app.core.security import require_verified_user
from app.modules.realtime.presence import get_presence_backend

router = APIRouter(
    prefix="/realtime",
    tags=["realtime"],
    responses=build_error_responses(400, 401, 422, 500, 502),
)


@router.get("/online-users", response_model=SuccessResponse[list[str]])
async def online_users(
    request: Request,
    user: dict = Depends(require_verified_user),
):
    presence = get_presence_backend()
    users = await presence.get_online_user_ids()
    return ok(request, data=users)


@router.get("/presence", response_model=SuccessResponse[dict])
async def presence_status(
    request: Request,
    user_ids: list[str] = Query(...),
    user: dict = Depends(require_verified_user),
):
    presence = get_presence_backend()
    data = {user_id: await presence.is_online(user_id) for user_id in user_ids}
    return ok(request, data=data)