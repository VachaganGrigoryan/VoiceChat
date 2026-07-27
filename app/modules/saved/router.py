from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from starlette.requests import Request

from app.core.errors.openapi import build_error_responses
from app.core.http import SuccessResponse, ok
from app.core.rate_limit import rate_limit
from app.core.security import require_verified_user
from app.modules.saved.dependencies import get_saved_messages_service
from app.modules.saved.schemas import SaveMessageRequest, SavedMessageView
from app.modules.saved.service import SavedMessagesService

router = APIRouter(
    prefix="/me/saved-messages",
    tags=["saved-messages"],
    responses=build_error_responses(400, 401, 404, 422, 500),
)


@router.post(
    "",
    status_code=201,
    response_model=SuccessResponse[SavedMessageView],
    dependencies=[Depends(rate_limit("60/minute", scope="saved_message_add"))],
)
async def save_message(
    request: Request,
    body: SaveMessageRequest,
    user=Depends(require_verified_user),
    service: SavedMessagesService = Depends(get_saved_messages_service),
):
    saved = await service.save_message(
        user_id=user.str_id,
        container_type=body.container_type,
        container_id=body.container_id,
        message_id=body.message_id,
    )
    return ok(request, data=saved, status_code=201)


@router.get(
    "",
    response_model=SuccessResponse[list[SavedMessageView]],
    dependencies=[Depends(rate_limit("60/minute", scope="saved_messages_list"))],
)
async def list_saved_messages(
    request: Request,
    limit: int = Query(50, ge=1, le=100),
    user=Depends(require_verified_user),
    service: SavedMessagesService = Depends(get_saved_messages_service),
):
    items = await service.list_saved(user_id=user.str_id, limit=limit)
    return ok(request, data=items)


@router.delete(
    "/{message_id}",
    status_code=204,
    dependencies=[Depends(rate_limit("60/minute", scope="saved_message_remove"))],
)
async def remove_saved_message(
    request: Request,
    message_id: str,
    user=Depends(require_verified_user),
    service: SavedMessagesService = Depends(get_saved_messages_service),
):
    await service.remove_saved(user_id=user.str_id, message_id=message_id)
    return ok(request, data=None, status_code=204)
