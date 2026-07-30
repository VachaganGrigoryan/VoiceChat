from __future__ import annotations

from typing import Annotated, Literal

import socketio
from fastapi import APIRouter, Depends, Query
from starlette.requests import Request

from app.core.deps import get_sio
from app.core.errors.openapi import build_error_responses
from app.core.http import SuccessResponse, ok
from app.core.http import PaginationMeta, PaginatedResponse, ok_paginated
from app.core.security import get_current_user_id
from app.db.models import RelationshipDocument
from app.modules.authorization import AuthorizationService
from app.modules.authorization.permissions import MEMBER_INVITE
from app.modules.authorization.router import get_authorization_service
from app.modules.relationships.connections import ConnectionService
from app.modules.relationships.dependencies import (
    get_connection_service,
    get_follow_service,
    get_membership_service,
)
from app.modules.relationships.follows import FollowService
from app.modules.relationships.memberships import MembershipService
from app.modules.relationships.schemas import (
    ConnectionDirection,
    ConnectionListItem,
    RelationshipView,
    to_relationship_view,
)
from app.modules.realtime import (
    emit_capabilities_invalidated,
    emit_relationship_activated,
    emit_relationship_requested,
    emit_relationship_revoked,
)

_RESPONSES = build_error_responses(400, 401, 403, 404, 409, 422, 500)

connections_router = APIRouter(
    prefix="/connections", tags=["connections"], responses=_RESPONSES
)
follows_router = APIRouter(tags=["follows"], responses=_RESPONSES)
memberships_router = APIRouter(tags=["memberships"], responses=_RESPONSES)


def _audience(doc: RelationshipDocument) -> list[str]:
    """Users who should see a lifecycle event for this edge.

    User-targeted edges reach both sides; resource-targeted ones reach the
    subject only (resource-wide fan-out is the resource module's concern).
    """
    if doc.target_type == "user":
        return [str(doc.user_id), str(doc.target_id)]
    return [str(doc.user_id)]


async def _emit_lifecycle(
    sio: socketio.AsyncServer, doc: RelationshipDocument
) -> None:
    payload = to_relationship_view(doc).model_dump(mode="json")
    audience = _audience(doc)
    if doc.status == "active":
        await emit_relationship_activated(
            sio, to_user_ids=audience, payload=payload
        )
    elif doc.status in {"declined", "revoked"}:
        await emit_relationship_revoked(sio, to_user_ids=audience, payload=payload)
    else:
        await emit_relationship_requested(sio, to_user_ids=audience, payload=payload)

    # A membership transition changes what the subject may do in that resource,
    # so any capabilities they have cached for it are now wrong.
    if doc.kind == "membership" and doc.target_type in {
        "space",
        "channel",
        "conversation",
    }:
        await emit_capabilities_invalidated(
            sio,
            to_user_ids=[str(doc.user_id)],
            resource_type=doc.target_type,
            resource_id=str(doc.target_id),
        )


# --- Connections (§80) -------------------------------------------------------


@connections_router.post(
    "/{user_id}/ping", status_code=201, response_model=SuccessResponse[RelationshipView]
)
async def ping_user(
    request: Request,
    user_id: str,
    sio: Annotated[socketio.AsyncServer, Depends(get_sio)],
    current_user_id: str = Depends(get_current_user_id),
    service: ConnectionService = Depends(get_connection_service),
):
    doc = await service.request(from_user_id=current_user_id, to_user_id=user_id)
    await _emit_lifecycle(sio, doc)
    return ok(request, data=to_relationship_view(doc), status_code=201)


@connections_router.post(
    "/{relationship_id}/accept", response_model=SuccessResponse[RelationshipView]
)
async def accept_connection(
    request: Request,
    relationship_id: str,
    sio: Annotated[socketio.AsyncServer, Depends(get_sio)],
    current_user_id: str = Depends(get_current_user_id),
    service: ConnectionService = Depends(get_connection_service),
):
    doc = await service.accept(
        user_id=current_user_id, relationship_id=relationship_id
    )
    await _emit_lifecycle(sio, doc)
    return ok(request, data=to_relationship_view(doc))


@connections_router.post(
    "/{relationship_id}/decline", response_model=SuccessResponse[RelationshipView]
)
async def decline_connection(
    request: Request,
    relationship_id: str,
    sio: Annotated[socketio.AsyncServer, Depends(get_sio)],
    current_user_id: str = Depends(get_current_user_id),
    service: ConnectionService = Depends(get_connection_service),
):
    doc = await service.decline(
        user_id=current_user_id, relationship_id=relationship_id
    )
    await _emit_lifecycle(sio, doc)
    return ok(request, data=to_relationship_view(doc))


@connections_router.delete(
    "/{relationship_id}", response_model=SuccessResponse[RelationshipView]
)
async def revoke_connection(
    request: Request,
    relationship_id: str,
    sio: Annotated[socketio.AsyncServer, Depends(get_sio)],
    current_user_id: str = Depends(get_current_user_id),
    service: ConnectionService = Depends(get_connection_service),
):
    doc = await service.revoke(
        user_id=current_user_id, relationship_id=relationship_id
    )
    await _emit_lifecycle(sio, doc)
    return ok(request, data=to_relationship_view(doc))


@connections_router.get(
    "", response_model=PaginatedResponse[list[ConnectionListItem]]
)
async def list_connections(
    request: Request,
    limit: int = Query(20, ge=1, le=100),
    cursor: str | None = Query(default=None),
    current_user_id: str = Depends(get_current_user_id),
    service: ConnectionService = Depends(get_connection_service),
):
    items, next_cursor = await service.list_connection_items(
        user_id=current_user_id,
        status="active",
        direction=None,
        limit=limit,
        cursor=cursor,
    )
    return ok_paginated(
        request,
        data=items,
        meta=PaginationMeta(
            cursor=cursor,
            next_cursor=next_cursor,
            limit=limit,
        ),
    )


@connections_router.get(
    "/pending", response_model=PaginatedResponse[list[ConnectionListItem]]
)
async def list_pending_connections(
    request: Request,
    direction: ConnectionDirection | None = Query(default=None),
    limit: int = Query(20, ge=1, le=100),
    cursor: str | None = Query(default=None),
    current_user_id: str = Depends(get_current_user_id),
    service: ConnectionService = Depends(get_connection_service),
):
    items, next_cursor = await service.list_connection_items(
        user_id=current_user_id,
        status="pending",
        direction=direction,
        limit=limit,
        cursor=cursor,
    )
    return ok_paginated(
        request,
        data=items,
        meta=PaginationMeta(
            cursor=cursor,
            next_cursor=next_cursor,
            limit=limit,
        ),
    )


# --- Follows (§81) -----------------------------------------------------------


@follows_router.post(
    "/users/{user_id}/follow",
    status_code=201,
    response_model=SuccessResponse[RelationshipView],
)
async def follow_user(
    request: Request,
    user_id: str,
    sio: Annotated[socketio.AsyncServer, Depends(get_sio)],
    current_user_id: str = Depends(get_current_user_id),
    service: FollowService = Depends(get_follow_service),
):
    doc = await service.follow(
        user_id=current_user_id, target_type="user", target_id=user_id
    )
    await _emit_lifecycle(sio, doc)
    return ok(request, data=to_relationship_view(doc), status_code=201)


@follows_router.delete("/users/{user_id}/follow", response_model=SuccessResponse[bool])
async def unfollow_user(
    request: Request,
    user_id: str,
    current_user_id: str = Depends(get_current_user_id),
    service: FollowService = Depends(get_follow_service),
):
    removed = await service.unfollow(
        user_id=current_user_id, target_type="user", target_id=user_id
    )
    return ok(request, data=removed)


@follows_router.get(
    "/users/{user_id}/followers", response_model=SuccessResponse[list[RelationshipView]]
)
async def list_user_followers(
    request: Request,
    user_id: str,
    limit: int = Query(50, ge=1, le=100),
    _: str = Depends(get_current_user_id),
    service: FollowService = Depends(get_follow_service),
):
    docs = await service.list_followers(
        target_type="user", target_id=user_id, limit=limit
    )
    return ok(request, data=[to_relationship_view(doc) for doc in docs])


@follows_router.get(
    "/users/{user_id}/following", response_model=SuccessResponse[list[RelationshipView]]
)
async def list_user_following(
    request: Request,
    user_id: str,
    limit: int = Query(50, ge=1, le=100),
    _: str = Depends(get_current_user_id),
    service: FollowService = Depends(get_follow_service),
):
    docs = await service.list_following(user_id=user_id, limit=limit)
    return ok(request, data=[to_relationship_view(doc) for doc in docs])


@follows_router.post(
    "/follows/{relationship_id}/accept",
    response_model=SuccessResponse[RelationshipView],
)
async def accept_follow(
    request: Request,
    relationship_id: str,
    sio: Annotated[socketio.AsyncServer, Depends(get_sio)],
    current_user_id: str = Depends(get_current_user_id),
    service: FollowService = Depends(get_follow_service),
):
    doc = await service.accept(
        user_id=current_user_id,
        relationship_id=relationship_id,
    )
    await _emit_lifecycle(sio, doc)
    return ok(request, data=to_relationship_view(doc))


@follows_router.post(
    "/follows/{relationship_id}/decline",
    response_model=SuccessResponse[RelationshipView],
)
async def decline_follow(
    request: Request,
    relationship_id: str,
    sio: Annotated[socketio.AsyncServer, Depends(get_sio)],
    current_user_id: str = Depends(get_current_user_id),
    service: FollowService = Depends(get_follow_service),
):
    doc = await service.decline(
        user_id=current_user_id,
        relationship_id=relationship_id,
    )
    await _emit_lifecycle(sio, doc)
    return ok(request, data=to_relationship_view(doc))


@follows_router.post(
    "/channels/{channel_id}/follow",
    status_code=201,
    response_model=SuccessResponse[RelationshipView],
)
async def follow_channel(
    request: Request,
    channel_id: str,
    sio: Annotated[socketio.AsyncServer, Depends(get_sio)],
    current_user_id: str = Depends(get_current_user_id),
    service: FollowService = Depends(get_follow_service),
):
    doc = await service.follow(
        user_id=current_user_id, target_type="channel", target_id=channel_id
    )
    await _emit_lifecycle(sio, doc)
    return ok(request, data=to_relationship_view(doc), status_code=201)


@follows_router.delete(
    "/channels/{channel_id}/follow", response_model=SuccessResponse[bool]
)
async def unfollow_channel(
    request: Request,
    channel_id: str,
    current_user_id: str = Depends(get_current_user_id),
    service: FollowService = Depends(get_follow_service),
):
    removed = await service.unfollow(
        user_id=current_user_id, target_type="channel", target_id=channel_id
    )
    return ok(request, data=removed)


# --- Memberships (§82) -------------------------------------------------------

_TARGET_BY_PREFIX: dict[str, Literal["space", "conversation", "channel"]] = {
    "spaces": "space",
    "conversations": "conversation",
    "channels": "channel",
}


def _register_membership_routes(prefix: str) -> None:
    """Declare the identical join/invite/accept/decline set per resource type.

    Space, group, and channel membership differ only in `target_type`, so the
    routes are generated rather than triplicated (§82). `POST /spaces/{id}/join`
    is skipped: that path already exists on the spaces router and keeps its
    `SpaceJoinRequestView` contract, now relationship-backed.
    """
    target_type = _TARGET_BY_PREFIX[prefix]

    if target_type != "space":

        @memberships_router.post(
            f"/{prefix}/{{target_id}}/join",
            status_code=201,
            response_model=SuccessResponse[RelationshipView],
            name=f"join_{target_type}",
        )
        async def join(  # type: ignore[misc]
            request: Request,
            target_id: str,
            sio: Annotated[socketio.AsyncServer, Depends(get_sio)],
            current_user_id: str = Depends(get_current_user_id),
            service: MembershipService = Depends(get_membership_service),
        ):
            doc = await service.request_join(
                user_id=current_user_id, target_type=target_type, target_id=target_id
            )
            await _emit_lifecycle(sio, doc)
            return ok(request, data=to_relationship_view(doc), status_code=201)

    @memberships_router.post(
        f"/{prefix}/{{target_id}}/invite/{{user_id}}",
        status_code=201,
        response_model=SuccessResponse[RelationshipView],
        name=f"invite_to_{target_type}",
    )
    async def invite(  # type: ignore[misc]
        request: Request,
        target_id: str,
        user_id: str,
        sio: Annotated[socketio.AsyncServer, Depends(get_sio)],
        current_user_id: str = Depends(get_current_user_id),
        service: MembershipService = Depends(get_membership_service),
        authorization: AuthorizationService = Depends(get_authorization_service),
    ):
        # Inviting is an authority over the target, not something any
        # authenticated user may do to any resource. `target_type` is always a
        # valid `ResourceType`, so one gate covers all three mounts.
        await authorization.require(
            current_user_id,
            MEMBER_INVITE,
            target_type,
            target_id,
            message="Not allowed to invite members to this resource",
        )
        doc = await service.invite(
            user_id=user_id,
            target_type=target_type,
            target_id=target_id,
            invited_by=current_user_id,
        )
        await _emit_lifecycle(sio, doc)
        return ok(request, data=to_relationship_view(doc), status_code=201)

    @memberships_router.post(
        f"/{prefix}/{{target_id}}/members/{{relationship_id}}/accept",
        response_model=SuccessResponse[RelationshipView],
        name=f"accept_{target_type}_membership",
    )
    async def accept(  # type: ignore[misc]
        request: Request,
        target_id: str,
        relationship_id: str,
        sio: Annotated[socketio.AsyncServer, Depends(get_sio)],
        current_user_id: str = Depends(get_current_user_id),
        service: MembershipService = Depends(get_membership_service),
    ):
        doc = await service.accept(
            user_id=current_user_id, relationship_id=relationship_id
        )
        await _emit_lifecycle(sio, doc)
        return ok(request, data=to_relationship_view(doc))

    @memberships_router.post(
        f"/{prefix}/{{target_id}}/members/{{relationship_id}}/decline",
        response_model=SuccessResponse[RelationshipView],
        name=f"decline_{target_type}_membership",
    )
    async def decline(  # type: ignore[misc]
        request: Request,
        target_id: str,
        relationship_id: str,
        sio: Annotated[socketio.AsyncServer, Depends(get_sio)],
        current_user_id: str = Depends(get_current_user_id),
        service: MembershipService = Depends(get_membership_service),
    ):
        doc = await service.decline(
            user_id=current_user_id, relationship_id=relationship_id
        )
        await _emit_lifecycle(sio, doc)
        return ok(request, data=to_relationship_view(doc))


for _prefix in _TARGET_BY_PREFIX:
    _register_membership_routes(_prefix)
