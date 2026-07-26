from __future__ import annotations

from fastapi import APIRouter, Depends, File, Query, UploadFile
from starlette.requests import Request

from app.core.errors.openapi import build_error_responses
from app.core.http import (
    PaginatedResponse,
    PaginationMeta,
    SuccessResponse,
    ok,
    ok_paginated,
)
from app.core.security import get_current_user_id
from app.modules.auth.repository import UsersRepository
from app.modules.channels.dependencies import get_channel_service
from app.modules.channels.schemas import ChannelMessageCreateRequest
from app.modules.channels.service import ChannelService
from app.modules.feeds.dependencies import get_feeds_service
from app.modules.feeds.schemas import FeedPostView
from app.modules.feeds.service import FeedsService
from app.modules.messages.schemas import MessageDoc
from app.modules.pings.dependencies import get_pings_service
from app.modules.realtime.presence.factory import get_presence_backend
from app.modules.channels.repository import ChannelsRepository
from app.modules.users.schemas import (
    SelectedUserProfileResponse,
    SetMainChannelRequest,
    UpdateProfileRequest,
    UpdateStatusRequest,
    UpdateUsernameRequest,
    UserChannelView,
    UserProfileResponse,
)
from app.modules.users.service import UsersService


router = APIRouter(
    prefix="/users",
    tags=["users"],
    responses=build_error_responses(400, 401, 403, 404, 409, 422, 500),
)


def get_users_service() -> UsersService:
    return UsersService(
        UsersRepository(),
        get_pings_service(),
        get_presence_backend(),
        ChannelsRepository(),
    )


@router.get("/me", response_model=SuccessResponse[UserProfileResponse])
async def get_me(
    request: Request,
    current_user_id: str = Depends(get_current_user_id),
    service: UsersService = Depends(get_users_service),
):
    result = await service.get_me(user_id=current_user_id)
    return ok(request, data=result)


@router.patch("/me/status", response_model=SuccessResponse[UserProfileResponse])
async def update_my_status(
    request: Request,
    body: UpdateStatusRequest,
    current_user_id: str = Depends(get_current_user_id),
    service: UsersService = Depends(get_users_service),
):
    result = await service.update_status(user_id=current_user_id, body=body)
    return ok(request, data=result)


@router.delete("/me/status", response_model=SuccessResponse[UserProfileResponse])
async def clear_my_status(
    request: Request,
    current_user_id: str = Depends(get_current_user_id),
    service: UsersService = Depends(get_users_service),
):
    result = await service.clear_status(user_id=current_user_id)
    return ok(request, data=result)


@router.patch("/me", response_model=SuccessResponse[UserProfileResponse])
async def update_me(
    request: Request,
    body: UpdateProfileRequest,
    current_user_id: str = Depends(get_current_user_id),
    service: UsersService = Depends(get_users_service),
):
    result = await service.update_me(user_id=current_user_id, body=body)
    return ok(request, data=result)


@router.patch("/me/username", response_model=SuccessResponse[UserProfileResponse])
async def update_my_username(
    request: Request,
    body: UpdateUsernameRequest,
    current_user_id: str = Depends(get_current_user_id),
    service: UsersService = Depends(get_users_service),
):
    result = await service.update_username(
        user_id=current_user_id,
        username=body.username,
    )
    return ok(request, data=result)


@router.patch("/me/main-channel", response_model=SuccessResponse[UserProfileResponse])
async def set_my_main_channel(
    request: Request,
    body: SetMainChannelRequest,
    current_user_id: str = Depends(get_current_user_id),
    service: UsersService = Depends(get_users_service),
):
    result = await service.set_main_channel(
        user_id=current_user_id, channel_id=body.channel_id
    )
    return ok(request, data=result)


@router.patch("/me/avatar", response_model=SuccessResponse[UserProfileResponse])
async def upload_my_avatar(
    request: Request,
    file: UploadFile = File(...),
    current_user_id: str = Depends(get_current_user_id),
    service: UsersService = Depends(get_users_service),
):
    result = await service.upload_avatar(user_id=current_user_id, file=file)
    return ok(request, data=result)


@router.delete("/me/avatar", response_model=SuccessResponse[UserProfileResponse])
async def delete_my_avatar(
    request: Request,
    current_user_id: str = Depends(get_current_user_id),
    service: UsersService = Depends(get_users_service),
):
    result = await service.delete_avatar(user_id=current_user_id)
    return ok(request, data=result)


@router.post(
    "/me/posts",
    status_code=201,
    response_model=SuccessResponse[MessageDoc],
)
async def create_my_profile_post(
    request: Request,
    body: ChannelMessageCreateRequest,
    current_user_id: str = Depends(get_current_user_id),
    service: ChannelService = Depends(get_channel_service),
):
    result = await service.create_profile_message(
        user_id=current_user_id,
        text=body.text,
        reply_mode=body.reply_mode,
        reply_to_message_id=body.reply_to_message_id,
    )
    return ok(request, data=result, status_code=201)


@router.get(
    "/{username}/posts",
    response_model=PaginatedResponse[list[FeedPostView]],
)
async def list_profile_posts(
    request: Request,
    username: str,
    limit: int = Query(default=20, ge=1, le=100),
    cursor: str | None = Query(default=None),
    current_user_id: str = Depends(get_current_user_id),
    service: FeedsService = Depends(get_feeds_service),
):
    items, next_cursor = await service.list_profile_posts(
        viewer_id=current_user_id,
        username=username,
        limit=limit,
        cursor=cursor,
    )
    return ok_paginated(
        request,
        data=items,
        meta=PaginationMeta(cursor=cursor, next_cursor=next_cursor, limit=limit),
    )


@router.get(
    "/{user_id}/channels",
    response_model=SuccessResponse[list[UserChannelView]],
)
async def list_user_channels(
    request: Request,
    user_id: str,
    limit: int = Query(default=50, ge=1, le=50),
    offset: int = Query(default=0, ge=0),
    current_user_id: str = Depends(get_current_user_id),
    service: UsersService = Depends(get_users_service),
):
    result = await service.list_user_channels(
        user_id=user_id, limit=limit, offset=offset
    )
    return ok(request, data=result)


@router.get("/{id}", response_model=SuccessResponse[SelectedUserProfileResponse])
async def get_user_profile(
    request: Request,
    id: str,
    include: str | None = Query(
        default=None,
        description="Comma-separated extra sections to embed (e.g. 'contact_details').",
    ),
    current_user_id: str = Depends(get_current_user_id),
    service: UsersService = Depends(get_users_service),
):
    include_tokens = (
        {token.strip() for token in include.split(",") if token.strip()}
        if include
        else None
    )
    result = await service.get_user_profile(
        current_user_id=current_user_id,
        selected_user_id=id,
        include=include_tokens,
    )
    return ok(request, data=result)
