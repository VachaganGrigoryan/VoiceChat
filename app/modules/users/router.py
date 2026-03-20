from __future__ import annotations

from fastapi import APIRouter, Depends, UploadFile, File
from starlette.requests import Request

from app.core.errors.openapi import build_error_responses
from app.core.http import SuccessResponse, ok
from app.core.security import get_current_user_id
from app.db.mongo import get_db
from app.modules.auth.repository import UsersRepository
from app.modules.users.schemas import (
    UpdateProfileRequest,
    UpdateUsernameRequest,
    UserProfileResponse,
)
from app.modules.users.service import UsersService


router = APIRouter(
    prefix="/users",
    tags=["users"],
    responses=build_error_responses(400, 401, 404, 409, 422, 500),
)


def get_users_service() -> UsersService:
    db = get_db()
    return UsersService(UsersRepository(db))


@router.get("/me", response_model=SuccessResponse[UserProfileResponse])
async def get_me(
    request: Request,
    current_user_id: str = Depends(get_current_user_id),
    service: UsersService = Depends(get_users_service),
):
    result = await service.get_me(user_id=current_user_id)
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