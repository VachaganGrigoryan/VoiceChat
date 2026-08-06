from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from starlette.requests import Request

from app.core.http import ok, SuccessResponse
from app.core.errors.openapi import build_error_responses
from app.core.security import get_current_user_id
from app.modules.auth.repository import UsersRepository
from app.modules.relationships.dependencies import get_connection_service
from app.modules.realtime.presence import get_presence_backend
from app.modules.realtime.schemas import PresenceStatusResponse

router = APIRouter(
    prefix="/realtime",
    tags=["realtime"],
    responses=build_error_responses(400, 401, 422, 500, 502),
)


@router.get("/online-users", response_model=SuccessResponse[list[str]])
async def online_users(
    request: Request,
    current_user_id: str = Depends(get_current_user_id),
):
    presence = get_presence_backend()
    user_ids = await presence.get_online_user_ids()
    users_repo = UsersRepository()
    connections = get_connection_service()
    users_by_id = await users_repo.find_by_ids(user_ids)
    visible_user_ids: list[str] = []
    for user_id in user_ids:
        if user_id == current_user_id:
            visible_user_ids.append(user_id)
            continue

        target = users_by_id.get(user_id)
        if target is None:
            continue

        relationship = await connections.get_connection_state(
            viewer_user_id=current_user_id,
            peer_user_id=user_id,
        )
        is_private = bool(getattr(target, "is_private", False))
        if (
            not relationship.blocks_me
            and not relationship.blocked_by_me
            and (not is_private or relationship.chat_allowed)
        ):
            visible_user_ids.append(user_id)
    return ok(request, data=visible_user_ids)


@router.get("/presence", response_model=SuccessResponse[dict[str, PresenceStatusResponse]])
async def presence_status(
    request: Request,
    user_ids: list[str] | None = Query(default=None),
    current_user_id: str = Depends(get_current_user_id),
):
    requested = user_ids or []
    presence = get_presence_backend()
    users_repo = UsersRepository()
    connections = get_connection_service()
    users_by_id = await users_repo.find_by_ids(list(dict.fromkeys(requested)))

    data: dict[str, PresenceStatusResponse] = {}
    for user_id in requested:
        target = users_by_id.get(user_id)
        state = await presence.get_state(user_id)
        can_view_presence = current_user_id == user_id
        if target is not None and not can_view_presence:
            relationship = await connections.get_connection_state(
                viewer_user_id=current_user_id,
                peer_user_id=user_id,
            )
            is_private = bool(getattr(target, "is_private", False))
            can_view_presence = (
                not relationship.blocks_me
                and not relationship.blocked_by_me
                and (not is_private or relationship.chat_allowed)
            )

        last_seen_at: datetime | None = None
        if can_view_presence and target is not None and state in {"away", "offline"}:
            last_seen_at = getattr(target, "last_seen_at", None)
        data[user_id] = PresenceStatusResponse(
            user_id=user_id,
            state=state if can_view_presence else "offline",
            is_online=state != "offline" if can_view_presence else False,
            last_seen_at=last_seen_at,
        )
    return ok(request, data=data)
