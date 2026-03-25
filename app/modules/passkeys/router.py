from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, status
from starlette.requests import Request

from app.core.errors.openapi import build_error_responses
from app.core.http import SuccessResponse, ok
from app.core.security import get_current_user
from app.modules.passkeys.schemas import (
    AuthTokensResponse,
    LoginPasskeyFinishRequest,
    LoginPasskeyStartRequest,
    PasskeyAuthenticationOptionsPayload,
    PasskeyDeleteResult,
    PasskeyRegistrationOptionsPayload,
    PasskeyResponse,
    RegisterPasskeyFinishRequest,
    RegisterPasskeyStartRequest,
)
from app.modules.passkeys.service import PasskeyService
from app.modules.passkeys.dependencies import get_passkey_service

router = APIRouter(
    prefix="/auth/passkeys",
    tags=["passkeys"],
    responses=build_error_responses(400, 401, 403, 404, 409, 422, 500),
)


@router.post(
    "/register/start", response_model=SuccessResponse[PasskeyRegistrationOptionsPayload]
)
async def start_registration(
    request: Request,
    payload: RegisterPasskeyStartRequest,
    current_user: dict[str, Any] = Depends(get_current_user),
    service: PasskeyService = Depends(get_passkey_service),
) -> Any:
    user_id = str(current_user.get("_id") or current_user["id"])
    options = await service.start_registration(
        user_id=user_id, nickname=payload.nickname
    )
    return ok(request, data=PasskeyRegistrationOptionsPayload.model_validate(options))


@router.post("/register/finish", response_model=SuccessResponse[PasskeyResponse])
async def finish_registration(
    request: Request,
    payload: RegisterPasskeyFinishRequest,
    current_user: dict[str, Any] = Depends(get_current_user),
    service: PasskeyService = Depends(get_passkey_service),
) -> Any:
    user_id = str(current_user.get("_id") or current_user["id"])
    passkey = await service.finish_registration(
        user_id=user_id,
        credential=payload.credential,
        nickname=payload.nickname,
    )
    return ok(request, data=passkey)


@router.post(
    "/login/start", response_model=SuccessResponse[PasskeyAuthenticationOptionsPayload]
)
async def start_login(
    request: Request,
    payload: LoginPasskeyStartRequest,
    service: PasskeyService = Depends(get_passkey_service),
) -> Any:
    options = await service.start_authentication(email=payload.email)
    return ok(request, data=PasskeyAuthenticationOptionsPayload.model_validate(options))


@router.post("/login/finish", response_model=SuccessResponse[AuthTokensResponse])
async def finish_login(
    request: Request,
    payload: LoginPasskeyFinishRequest,
    service: PasskeyService = Depends(get_passkey_service),
) -> Any:
    tokens = await service.finish_authentication(
        credential=payload.credential, email=payload.email
    )
    return ok(request, data=AuthTokensResponse(**tokens))


@router.get("", response_model=SuccessResponse[list[PasskeyResponse]])
async def list_passkeys(
    request: Request,
    current_user: dict[str, Any] = Depends(get_current_user),
    service: PasskeyService = Depends(get_passkey_service),
) -> Any:
    user_id = str(current_user.get("_id") or current_user["id"])
    return ok(request, data=await service.list_passkeys(user_id=user_id))


@router.delete(
    "/{credential_id}",
    response_model=SuccessResponse[PasskeyDeleteResult],
    status_code=status.HTTP_200_OK,
)
async def delete_passkey(
    request: Request,
    credential_id: str,
    current_user: dict[str, Any] = Depends(get_current_user),
    service: PasskeyService = Depends(get_passkey_service),
) -> Any:
    user_id = str(current_user.get("_id") or current_user["id"])
    deleted = await service.delete_passkey(user_id=user_id, credential_id=credential_id)
    return ok(request, data=PasskeyDeleteResult(deleted=deleted))
