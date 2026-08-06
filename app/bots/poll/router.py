from __future__ import annotations

from typing import Annotated

import socketio
from fastapi import APIRouter, Depends
from starlette.requests import Request

from app.bots.poll.dependencies import get_poll_service
from app.bots.poll.schemas import (
    CreatePollRequest,
    CreatePollResponse,
    PollVoteRequest,
    PollView,
)
from app.bots.poll.service import PollService
from app.core.deps import get_sio
from app.core.errors.openapi import build_error_responses
from app.core.http import SuccessResponse, ok
from app.core.rate_limit import rate_limit
from app.core.security import require_verified_user
from app.modules.messages.emit_helpers import emit_send_result
from app.modules.realtime import emit_poll_updated

router = APIRouter(
    prefix="/polls",
    tags=["polls"],
    responses=build_error_responses(400, 401, 403, 404, 409, 422, 500),
)


@router.post(
    "",
    status_code=201,
    response_model=SuccessResponse[CreatePollResponse],
    dependencies=[Depends(rate_limit("30/minute", scope="poll_create"))],
)
async def create_poll(
    request: Request,
    body: CreatePollRequest,
    sio: Annotated[socketio.AsyncServer, Depends(get_sio)],
    user=Depends(require_verified_user),
    service: PollService = Depends(get_poll_service),
):
    outcome = await service.create_poll(user_id=user.str_id, body=body)
    await emit_send_result(
        sio,
        result=outcome.send_result,
        participant_ids=outcome.participant_ids,
    )
    return ok(request, data=outcome.response, status_code=201)


@router.get("/{poll_id}", response_model=SuccessResponse[PollView])
async def get_poll(
    request: Request,
    poll_id: str,
    user=Depends(require_verified_user),
    service: PollService = Depends(get_poll_service),
):
    view = await service.get_poll(user_id=user.str_id, poll_id=poll_id)
    return ok(request, data=view)


@router.post(
    "/{poll_id}/vote",
    response_model=SuccessResponse[PollView],
    dependencies=[Depends(rate_limit("120/minute", scope="poll_vote"))],
)
async def vote_poll(
    request: Request,
    poll_id: str,
    body: PollVoteRequest,
    sio: Annotated[socketio.AsyncServer, Depends(get_sio)],
    user=Depends(require_verified_user),
    service: PollService = Depends(get_poll_service),
):
    outcome = await service.vote(
        user_id=user.str_id, poll_id=poll_id, option_ids=body.option_ids
    )
    await emit_poll_updated(
        sio,
        participant_ids=outcome.participant_ids,
        payload=outcome.broadcast_payload,
    )
    return ok(request, data=outcome.view)


@router.post("/{poll_id}/retract", response_model=SuccessResponse[PollView])
async def retract_vote(
    request: Request,
    poll_id: str,
    sio: Annotated[socketio.AsyncServer, Depends(get_sio)],
    user=Depends(require_verified_user),
    service: PollService = Depends(get_poll_service),
):
    outcome = await service.retract(user_id=user.str_id, poll_id=poll_id)
    await emit_poll_updated(
        sio,
        participant_ids=outcome.participant_ids,
        payload=outcome.broadcast_payload,
    )
    return ok(request, data=outcome.view)


@router.post("/{poll_id}/close", response_model=SuccessResponse[PollView])
async def close_poll(
    request: Request,
    poll_id: str,
    sio: Annotated[socketio.AsyncServer, Depends(get_sio)],
    user=Depends(require_verified_user),
    service: PollService = Depends(get_poll_service),
):
    outcome = await service.close(user_id=user.str_id, poll_id=poll_id)
    await emit_poll_updated(
        sio,
        participant_ids=outcome.participant_ids,
        payload=outcome.broadcast_payload,
    )
    return ok(request, data=outcome.view)
