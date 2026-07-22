from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from starlette.requests import Request

from app.core.errors.openapi import build_error_responses
from app.core.http import SuccessResponse, ok
from app.core.security import require_verified_user
from app.modules.conversations.repository.mappers import to_participant_view
from app.modules.conversations.schemas import ParticipantView
from app.modules.notifications.dependencies import get_notifications_service
from app.modules.notifications.schemas import (
    ConversationNotificationSettingsRequest,
    NotificationPreferencesRequest,
    NotificationView,
    PushTokenRegisterRequest,
    PushTokenView,
)
from app.modules.notifications.service import NotificationsService
from app.modules.users.service import UsersService
from app.modules.users.router import get_users_service
from app.modules.users.schemas import UserProfileResponse

router = APIRouter(
    prefix="/notifications",
    tags=["notifications"],
    responses=build_error_responses(400, 401, 404, 422, 500),
)


@router.get("", response_model=SuccessResponse[list[NotificationView]])
async def list_notifications(
    request: Request,
    limit: int = Query(50, ge=1, le=100),
    user=Depends(require_verified_user),
    service: NotificationsService = Depends(get_notifications_service),
):
    notifications = await service.list_for_user(user_id=user.str_id, limit=limit)
    return ok(request, data=notifications)


@router.patch(
    "/preferences",
    response_model=SuccessResponse[UserProfileResponse],
)
async def update_notification_preferences(
    request: Request,
    body: NotificationPreferencesRequest,
    user=Depends(require_verified_user),
    service: NotificationsService = Depends(get_notifications_service),
    users_service: UsersService = Depends(get_users_service),
):
    await service.update_preferences(user_id=user.str_id, body=body)
    profile = await users_service.get_me(user_id=user.str_id)
    return ok(request, data=profile)


@router.patch(
    "/conversations/{conversation_id}",
    response_model=SuccessResponse[ParticipantView],
)
async def update_conversation_notification_settings(
    request: Request,
    conversation_id: str,
    body: ConversationNotificationSettingsRequest,
    user=Depends(require_verified_user),
    service: NotificationsService = Depends(get_notifications_service),
):
    participant = await service.update_conversation_settings(
        conversation_id=conversation_id,
        user_id=user.str_id,
        notification_level=body.notification_level,
        muted_until=body.muted_until,
        set_muted_until="muted_until" in body.model_fields_set,
    )
    return ok(request, data=to_participant_view(participant))


@router.post(
    "/push-tokens",
    status_code=201,
    response_model=SuccessResponse[PushTokenView],
)
async def register_push_token(
    request: Request,
    body: PushTokenRegisterRequest,
    user=Depends(require_verified_user),
    service: NotificationsService = Depends(get_notifications_service),
):
    token = await service.register_push_token(user_id=user.str_id, body=body)
    return ok(request, data=token, status_code=201)


@router.delete("/push-tokens", response_model=SuccessResponse[dict[str, int]])
async def remove_push_token(
    request: Request,
    device_id: Annotated[str | None, Query(max_length=200)] = None,
    token: Annotated[str | None, Query(max_length=4096)] = None,
    user=Depends(require_verified_user),
    service: NotificationsService = Depends(get_notifications_service),
):
    deleted = await service.remove_push_token(
        user_id=user.str_id,
        device_id=device_id,
        token=token,
    )
    return ok(request, data={"deleted": deleted})
