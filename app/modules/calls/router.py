from __future__ import annotations

from typing import Annotated

import socketio
from fastapi import APIRouter, Depends
from starlette.requests import Request

from app.core.deps import get_sio
from app.core.errors.openapi import build_error_responses
from app.core.http import SuccessResponse, ok
from app.core.rate_limit import rate_limit
from app.core.security import require_verified_user
from app.modules.calls.dependencies import get_calls_service
from app.modules.calls.schemas import AcceptCallRequest, CallDoc, CallSession, CreateCallRequest
from app.modules.calls.service import CallsService
from app.modules.calls.ws import (
    bind_call_socket,
    cancel_call_expiration,
    cancel_call_reconnect_timeout,
    clear_call_bindings,
    emit_call_incoming_event,
    emit_call_state_event,
    ensure_socket_belongs_to_user,
    join_call_room,
    schedule_call_expiration,
)

router = APIRouter(
    prefix="/calls",
    tags=["calls"],
    responses=build_error_responses(400, 401, 403, 404, 409, 422, 500),
)


@router.post(
    "",
    status_code=201,
    response_model=SuccessResponse[CallSession],
    dependencies=[Depends(rate_limit("20/minute", scope="call_create"))],
)
async def create_call(
    request: Request,
    body: CreateCallRequest,
    sio: Annotated[socketio.AsyncServer, Depends(get_sio)],
    user: dict = Depends(require_verified_user),
    service: CallsService = Depends(get_calls_service),
):
    caller_user_id = str(user["_id"])
    call_doc = await service.create_call(
        caller_user_id=caller_user_id,
        callee_user_id=body.callee_user_id,
        call_type=body.type,
    )

    await emit_call_incoming_event(sio, service=service, call_doc=call_doc)

    caller_payload = await service.build_session(
        call_doc=call_doc,
        viewer_user_id=caller_user_id,
        include_ice_servers=True,
    )
    schedule_call_expiration(
        sio,
        call_id=caller_payload.call.id,
        expires_at=caller_payload.call.expires_at,
    )

    return ok(request, data=caller_payload, status_code=201)


@router.get("/active", response_model=SuccessResponse[CallSession | None])
async def get_active_call(
    request: Request,
    user: dict = Depends(require_verified_user),
    service: CallsService = Depends(get_calls_service),
):
    payload = await service.get_active_call_session(user_id=str(user["_id"]))
    return ok(request, data=payload)


@router.post("/{call_id}/accept", response_model=SuccessResponse[CallSession])
async def accept_call(
    request: Request,
    call_id: str,
    body: AcceptCallRequest,
    sio: Annotated[socketio.AsyncServer, Depends(get_sio)],
    user: dict = Depends(require_verified_user),
    service: CallsService = Depends(get_calls_service),
):
    user_id = str(user["_id"])
    await ensure_socket_belongs_to_user(sio, socket_id=body.socket_id, user_id=user_id)

    call_doc = await service.accept_call(user_id=user_id, call_id=call_id)
    await join_call_room(sio, socket_id=body.socket_id, room_id=call_doc["room_id"])
    await bind_call_socket(call_id=call_id, user_id=user_id, socket_id=body.socket_id)
    cancel_call_expiration(call_id)
    cancel_call_reconnect_timeout(call_id)

    await emit_call_state_event(
        sio,
        event="call.accepted",
        service=service,
        call_doc=call_doc,
        include_ice_servers=True,
    )

    payload = await service.build_session(
        call_doc=call_doc,
        viewer_user_id=user_id,
        include_ice_servers=True,
    )
    return ok(request, data=payload)


@router.post("/{call_id}/reject", response_model=SuccessResponse[CallDoc])
async def reject_call(
    request: Request,
    call_id: str,
    sio: Annotated[socketio.AsyncServer, Depends(get_sio)],
    user: dict = Depends(require_verified_user),
    service: CallsService = Depends(get_calls_service),
):
    call_doc = await service.reject_call(user_id=str(user["_id"]), call_id=call_id)
    model = service.to_call_doc(call_doc)
    cancel_call_expiration(call_id)
    cancel_call_reconnect_timeout(call_id)
    await clear_call_bindings(
        call_id=model.id,
        participant_user_ids=model.participant_user_ids,
    )
    await emit_call_state_event(
        sio,
        event="call.rejected",
        service=service,
        call_doc=call_doc,
    )
    return ok(request, data=model)


@router.post("/{call_id}/end", response_model=SuccessResponse[CallDoc])
async def end_call(
    request: Request,
    call_id: str,
    sio: Annotated[socketio.AsyncServer, Depends(get_sio)],
    user: dict = Depends(require_verified_user),
    service: CallsService = Depends(get_calls_service),
):
    call_doc = await service.end_call(user_id=str(user["_id"]), call_id=call_id)
    model = service.to_call_doc(call_doc)
    cancel_call_expiration(call_id)
    cancel_call_reconnect_timeout(call_id)
    await clear_call_bindings(
        call_id=model.id,
        participant_user_ids=model.participant_user_ids,
    )
    await emit_call_state_event(
        sio,
        event="call.ended",
        service=service,
        call_doc=call_doc,
    )
    return ok(request, data=model)
