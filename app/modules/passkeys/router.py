from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, status
from app.core.security import get_current_user
from app.modules.passkeys.schemas import (
    LoginPasskeyFinishRequest,
    LoginPasskeyStartRequest,
    PasskeyDeleteResponse,
    PasskeyRegistrationFinishResponse,
    PasskeysListResponse,
    RegisterPasskeyFinishRequest,
    RegisterPasskeyStartRequest,
)
from app.modules.passkeys.service import PasskeyService
from app.modules.passkeys.dependencies import get_passkey_service


router = APIRouter(prefix="/auth/passkeys", tags=["passkeys"])


@router.post("/register/start")
async def start_registration(
    payload: RegisterPasskeyStartRequest,
    current_user: dict[str, Any] = Depends(get_current_user),
    service: PasskeyService = Depends(get_passkey_service),
) -> dict[str, Any]:
    user_id = str(current_user.get("_id") or current_user["id"])
    return await service.start_registration(user_id=user_id, nickname=payload.nickname)


@router.post("/register/finish", response_model=PasskeyRegistrationFinishResponse)
async def finish_registration(
    payload: RegisterPasskeyFinishRequest,
    current_user: dict[str, Any] = Depends(get_current_user),
    service: PasskeyService = Depends(get_passkey_service),
) -> PasskeyRegistrationFinishResponse:
    user_id = str(current_user.get("_id") or current_user["id"])
    passkey = await service.finish_registration(
        user_id=user_id,
        credential=payload.credential,
        nickname=payload.nickname,
    )
    return PasskeyRegistrationFinishResponse(success=True, passkey=passkey)


@router.post("/login/start")
async def start_login(
    payload: LoginPasskeyStartRequest,
    service: PasskeyService = Depends(get_passkey_service),
) -> dict[str, Any]:
    return await service.start_authentication(email=payload.email)


@router.post("/login/finish")
async def finish_login(
    payload: LoginPasskeyFinishRequest,
    service: PasskeyService = Depends(get_passkey_service),
) -> dict[str, str]:
    return await service.finish_authentication(credential=payload.credential, email=payload.email)


@router.get("", response_model=PasskeysListResponse)
async def list_passkeys(
    current_user: dict[str, Any] = Depends(get_current_user),
    service: PasskeyService = Depends(get_passkey_service),
) -> PasskeysListResponse:
    user_id = str(current_user.get("_id") or current_user["id"])
    return PasskeysListResponse(items=await service.list_passkeys(user_id=user_id))


@router.delete("/{credential_id}", response_model=PasskeyDeleteResponse, status_code=status.HTTP_200_OK)
async def delete_passkey(
    credential_id: str,
    current_user: dict[str, Any] = Depends(get_current_user),
    service: PasskeyService = Depends(get_passkey_service),
) -> PasskeyDeleteResponse:
    user_id = str(current_user.get("_id") or current_user["id"])
    return PasskeyDeleteResponse(success=await service.delete_passkey(user_id=user_id, credential_id=credential_id))
