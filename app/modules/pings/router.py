from __future__ import annotations

from typing import Annotated

import socketio
from fastapi import APIRouter, Depends, Query
from starlette.requests import Request

from app.core.deps import get_sio
from app.core.http import PaginatedResponse, ok_paginated, PaginationMeta
from app.core.security import get_current_user_id
from app.modules.pings.dependencies import get_pings_service
from app.modules.pings.schemas import PingResponse, SendPingRequest, PingListItem, PeerActionRequest
from app.modules.realtime import emit_ping_received, emit_ping_accepted, emit_chat_permission_updated, \
    emit_ping_declined
from app.modules.realtime.emits import emit_ping_cancelled, emit_user_blocked

router = APIRouter(prefix="/pings", tags=["pings"])


@router.post("", response_model=PingResponse)
async def send_ping(
    body: SendPingRequest,
    sio: Annotated[socketio.AsyncServer, Depends(get_sio)],
    user_id=Depends(get_current_user_id),
    service=Depends(get_pings_service),
):
    ping = await service.send_ping(from_user_id=user_id, to_user_id=body.to_user_id)

    doc = await service.pings_repo.find_by_id(ping.id)
    if doc:
        payload = await service.to_realtime_payload(doc, incoming_for=body.to_user_id)
        await emit_ping_received(
            sio,
            to_user_id=body.to_user_id,
            payload=payload,
        )

    return ping


@router.get("/incoming", response_model=PaginatedResponse[list[PingListItem]])
async def list_incoming(
    request: Request,
    user_id=Depends(get_current_user_id),
    limit: int = Query(default=20, ge=1, le=100),
    cursor: str | None = Query(default=None),
    service=Depends(get_pings_service),
):
    items, next_cursor = await service.list_incoming(
        user_id=user_id,
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


@router.get("/outgoing", response_model=PaginatedResponse[list[PingListItem]])
async def list_outgoing(
    request: Request,
    user_id=Depends(get_current_user_id),
    limit: int = Query(default=20, ge=1, le=100),
    cursor: str | None = Query(default=None),
    service=Depends(get_pings_service),
):
    items, next_cursor = await service.list_outgoing(
        user_id=user_id,
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



@router.post("/{ping_id}/accept", response_model=PingResponse)
async def accept_ping(
    ping_id: str,
    sio: Annotated[socketio.AsyncServer, Depends(get_sio)],
    user_id=Depends(get_current_user_id),
    service=Depends(get_pings_service),
):
    ping = await service.accept_ping(user_id=user_id, ping_id=ping_id)

    doc = await service.pings_repo.find_by_id(ping.id)
    if doc:
        payload = await service.to_realtime_payload(doc, incoming_for=doc["from_user_id"])
        await emit_ping_accepted(
            sio,
            to_user_id=doc["from_user_id"],
            payload=payload,
        )
        await emit_chat_permission_updated(
            sio,
            user_a=doc["from_user_id"],
            user_b=doc["to_user_id"],
            allowed=True,
        )

    return ping

@router.post("/{ping_id}/decline", response_model=PingResponse)
async def decline_ping(
    ping_id: str,
    sio: Annotated[socketio.AsyncServer, Depends(get_sio)],
    user_id=Depends(get_current_user_id),
    service=Depends(get_pings_service),
):
    ping = await service.decline_ping(user_id=user_id, ping_id=ping_id)
    doc = await service.pings_repo.find_by_id(ping.id)
    if doc:
        payload = await service.to_realtime_payload(doc, incoming_for=doc["from_user_id"])
        await emit_ping_declined(
            sio,
            to_user_id=doc["from_user_id"],
            payload=payload,
        )

    return ping


@router.post("/{ping_id}/cancel", response_model=PingResponse)
async def cancel_ping(
        ping_id: str,
        sio: Annotated[socketio.AsyncServer, Depends(get_sio)],
        user_id=Depends(get_current_user_id),
        service=Depends(get_pings_service),
):
    ping = await service.cancel_ping(user_id=user_id, ping_id=ping_id)
    doc = await service.pings_repo.find_by_id(ping.id)
    if doc:
        payload = await service.to_realtime_payload(doc, incoming_for=doc["to_user_id"])
        await emit_ping_cancelled(sio, to_user_id=doc["to_user_id"], payload=payload)
    return ping


@router.post("/block", response_model=PingResponse)
async def block_user(
        body: PeerActionRequest,
        sio: Annotated[socketio.AsyncServer, Depends(get_sio)],
        user_id=Depends(get_current_user_id),
        service=Depends(get_pings_service),
):
    ping = await service.block_user(user_id=user_id, peer_user_id=body.peer_user_id)
    await emit_user_blocked(sio, user_a=user_id, user_b=body.peer_user_id)
    await emit_chat_permission_updated(sio, user_a=user_id, user_b=body.peer_user_id, allowed=False)
    return ping


@router.post("/unblock", response_model=PingResponse)
async def unblock_user(
        body: PeerActionRequest,
        sio: Annotated[socketio.AsyncServer, Depends(get_sio)],
        user_id=Depends(get_current_user_id),
        service=Depends(get_pings_service),
):
    ping = await service.unblock_user(user_id=user_id, peer_user_id=body.peer_user_id)
    await emit_chat_permission_updated(sio, user_a=user_id, user_b=body.peer_user_id, allowed=False)
    return ping


@router.get("/blocked", response_model=list[PingResponse])
async def blocked_users(
        user_id=Depends(get_current_user_id),
        service=Depends(get_pings_service),
):
    return await service.list_blocked(user_id=user_id)
