from __future__ import annotations

from fastapi import APIRouter, Depends
from starlette.requests import Request

from app.core.http import ok, SuccessResponse
from app.core.errors.openapi import build_error_responses
from app.core.rate_limit import rate_limit
from app.db.mongo import get_db
from app.modules.auth.schemas import (
    GenericEmailRequest,
    GenericCodeSentResponse,
    VerifyRequest,
    TokenPairResponse,
    RefreshRequest,
    MessageResponse,
    LogoutRequest,
)
from app.modules.auth.repository import UsersRepository
from app.modules.verification.repository import VerificationCodesRepository
from app.modules.auth.refresh_repository import RefreshTokensRepository
from app.modules.auth.service import AuthService

router = APIRouter(
    prefix="/auth",
    tags=["auth"],
    responses=build_error_responses(400, 422, 500),
    dependencies=[Depends(rate_limit("100/hour", scope="auth_global"))],
)


@router.post(
    "/register",
    response_model=SuccessResponse[GenericCodeSentResponse],
    status_code=201,
    dependencies=[Depends(rate_limit("5/15 minutes", scope="auth_register"))],
)
async def register(request: Request, body: GenericEmailRequest):
    db = get_db()
    service = AuthService(
        UsersRepository(db),
        VerificationCodesRepository(db),
        RefreshTokensRepository(db),
    )
    result = await service.register(email=body.email)
    return ok(request, data=GenericCodeSentResponse(**result))


@router.post(
    "/verify",
    response_model=SuccessResponse[TokenPairResponse],
    dependencies=[Depends(rate_limit("10/15 minutes", scope="auth_verify"))],
)
async def verify(request: Request, body: VerifyRequest):
    db = get_db()
    service = AuthService(
        UsersRepository(db),
        VerificationCodesRepository(db),
        RefreshTokensRepository(db),
    )
    result = await service.verify(email=body.email, code=body.code)
    return ok(request, data=TokenPairResponse(**result))


@router.post(
    "/login",
    response_model=SuccessResponse[GenericCodeSentResponse],
    dependencies=[Depends(rate_limit("5/15 minutes", scope="auth_login"))],
)
async def login(request: Request, body: GenericEmailRequest):
    db = get_db()
    service = AuthService(
        UsersRepository(db),
        VerificationCodesRepository(db),
        RefreshTokensRepository(db),
    )
    result = await service.login(email=body.email)
    return ok(request, data=GenericCodeSentResponse(**result))


@router.post(
    "/refresh",
    response_model=SuccessResponse[TokenPairResponse],
)
async def refresh(request: Request, body: RefreshRequest):
    db = get_db()
    service = AuthService(
        UsersRepository(db),
        VerificationCodesRepository(db),
        RefreshTokensRepository(db),
    )

    result = await service.refresh(
        refresh_token=body.refresh_token,
        user_agent=request.headers.get("user-agent"),
        ip=request.client.host if request.client else None,
    )
    return ok(request, data=TokenPairResponse(**result))


@router.post(
    "/logout",
    response_model=SuccessResponse[MessageResponse],
)
async def logout(request: Request, body: LogoutRequest):
    db = get_db()
    service = AuthService(
        UsersRepository(db),
        VerificationCodesRepository(db),
        RefreshTokensRepository(db),
    )

    result = await service.logout(refresh_token=body.refresh_token)
    return ok(request, data=MessageResponse(**result))
