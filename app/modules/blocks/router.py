from __future__ import annotations

from typing import Annotated

import socketio
from fastapi import APIRouter, Depends, Query, Response
from starlette.requests import Request

from app.core.deps import get_sio
from app.core.errors.openapi import build_error_responses
from app.core.http import (
    PaginationMeta,
    PaginatedResponse,
    SuccessResponse,
    ok,
    ok_paginated,
)
from app.core.security import get_current_user_id
from app.modules.blocks.dependencies import get_blocks_service
from app.modules.blocks.schemas import BlockView, BlockedUserListItem, to_block_view
from app.modules.blocks.service import BlocksService
from app.modules.relationships.schemas import to_relationship_view
from app.modules.realtime import emit_relationship_revoked
from app.modules.realtime.emits import emit_block_created, emit_block_removed


router = APIRouter(
    prefix="/blocks",
    tags=["blocks"],
    responses=build_error_responses(400, 401, 403, 404, 409, 422, 500),
)


@router.post(
    "/{user_id}",
    status_code=201,
    response_model=SuccessResponse[BlockView],
)
async def block_user(
    request: Request,
    user_id: str,
    sio: Annotated[socketio.AsyncServer, Depends(get_sio)],
    current_user_id: str = Depends(get_current_user_id),
    service: BlocksService = Depends(get_blocks_service),
):
    result = await service.block(
        blocker_id=current_user_id,
        blocked_id=user_id,
    )
    await emit_block_created(
        sio,
        to_user_id=current_user_id,
        payload=to_block_view(result.block).model_dump(mode="json"),
    )
    if result.revoked_relationship is not None:
        relationship = result.revoked_relationship
        await emit_relationship_revoked(
            sio,
            to_user_ids=[
                str(relationship.user_id),
                str(relationship.target_id),
            ],
            payload=to_relationship_view(relationship).model_dump(mode="json"),
        )
    return ok(
        request,
        data=to_block_view(result.block),
        status_code=201,
    )


@router.delete("/{user_id}", status_code=204)
async def unblock_user(
    user_id: str,
    sio: Annotated[socketio.AsyncServer, Depends(get_sio)],
    current_user_id: str = Depends(get_current_user_id),
    service: BlocksService = Depends(get_blocks_service),
) -> Response:
    block = await service.unblock(
        blocker_id=current_user_id,
        blocked_id=user_id,
    )
    await emit_block_removed(
        sio,
        to_user_id=current_user_id,
        payload=to_block_view(block).model_dump(mode="json"),
    )
    return Response(status_code=204)


@router.get("", response_model=PaginatedResponse[list[BlockedUserListItem]])
async def list_blocked_users(
    request: Request,
    limit: int = Query(20, ge=1, le=100),
    cursor: str | None = Query(default=None),
    current_user_id: str = Depends(get_current_user_id),
    service: BlocksService = Depends(get_blocks_service),
):
    items, next_cursor = await service.list_blocked_users(
        blocker_id=current_user_id,
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
