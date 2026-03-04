from __future__ import annotations

from fastapi import APIRouter

from app.core.openapi import COMMON_ERROR_RESPONSES
from app.db.mongo import get_db
from app.modules.auth.schemas import (
    RegisterRequest,
    RegisterResponse,
    VerifyRequest,
    VerifyResponse,
    LoginRequest,
    LoginResponse,
)
from app.modules.auth.repository import UsersRepository
from app.modules.verification.repository import VerificationCodesRepository
from app.modules.auth.service import AuthService

router = APIRouter(prefix="/auth", tags=["auth"], responses=COMMON_ERROR_RESPONSES)


@router.post("/register", response_model=RegisterResponse, status_code=201)
async def register(body: RegisterRequest):
    db = get_db()
    service = AuthService(UsersRepository(db), VerificationCodesRepository(db))
    return await service.register(email=body.email)


@router.post("/verify", response_model=VerifyResponse)
async def verify(body: VerifyRequest):
    db = get_db()
    service = AuthService(UsersRepository(db), VerificationCodesRepository(db))
    return await service.verify(email=body.email, code=body.code)


@router.post("/login", response_model=LoginResponse)
async def login(body: LoginRequest):
    db = get_db()
    service = AuthService(UsersRepository(db), VerificationCodesRepository(db))
    return await service.login(email=body.email)