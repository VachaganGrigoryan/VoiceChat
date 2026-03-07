from __future__ import annotations

from fastapi import APIRouter, Depends
from starlette.requests import Request

from app.core.api_models import SuccessResponse
from app.core.openapi import COMMON_ERROR_RESPONSES
from app.core.rate_limit_deps import rate_limit
from app.core.responses import ok
from app.db.mongo import get_db
from app.modules.auth.schemas import (
    GenericEmailRequest,
    GenericCodeSentResponse,
    VerifyRequest,
    VerifyResponse,
)
from app.modules.auth.repository import UsersRepository
from app.modules.verification.repository import VerificationCodesRepository
from app.modules.auth.service import AuthService

router = APIRouter(
    prefix="/auth",
    tags=["auth"],
    responses=COMMON_ERROR_RESPONSES,
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
    service = AuthService(UsersRepository(db), VerificationCodesRepository(db))
    result = await service.register(email=body.email)
    return ok(request, data=GenericCodeSentResponse(**result))


@router.post(
    "/verify",
    response_model=SuccessResponse[VerifyResponse],
    dependencies=[Depends(rate_limit("10/15 minutes", scope="auth_verify"))],
)
async def verify(request: Request, body: VerifyRequest):
    db = get_db()
    service = AuthService(UsersRepository(db), VerificationCodesRepository(db))
    result = await service.verify(email=body.email, code=body.code)
    return ok(request, data=VerifyResponse(**result))


@router.post(
    "/login",
    response_model=SuccessResponse[GenericCodeSentResponse],
    dependencies=[Depends(rate_limit("5/15 minutes", scope="auth_login"))],
)
async def login(request: Request, body: GenericEmailRequest):
    db = get_db()
    service = AuthService(UsersRepository(db), VerificationCodesRepository(db))
    result = await service.login(email=body.email)
    return ok(request, data=GenericCodeSentResponse(**result))
