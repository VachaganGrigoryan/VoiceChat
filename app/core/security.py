from __future__ import annotations

import hmac
import hashlib
import secrets
from datetime import datetime, timedelta, UTC
from typing import Any

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import jwt, JWTError

from app.core.config import settings
from app.core.errors import AppError
from app.db.mongo import get_db
from app.modules.auth.repository import UsersRepository

bearer = HTTPBearer(auto_error=False)


def create_access_token(*, subject: str, extra: dict[str, Any] | None = None) -> str:
    now = datetime.now(UTC)
    exp = now + timedelta(minutes=settings.access_token_expire_minutes)
    payload: dict[str, Any] = {
        "sub": subject,
        "iat": int(now.timestamp()),
        "exp": int(exp.timestamp()),
        "type": "access",
    }
    if extra:
        payload.update(extra)
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_alg)


def decode_token(token: str) -> dict[str, Any]:
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_alg])
    except JWTError:
        raise AppError(code="UNAUTHORIZED", message="Invalid or expired token", status_code=401)

    if payload.get("type") != "access":
        raise AppError(code="UNAUTHORIZED", message="Invalid token type", status_code=401)

    return payload


def generate_refresh_token() -> str:
    return secrets.token_urlsafe(48)


def hash_refresh_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


async def get_current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(bearer),
) -> dict[str, Any]:
    if creds is None or not creds.credentials:
        raise AppError(code="UNAUTHORIZED", message="Missing authorization token", status_code=401)

    payload = decode_token(creds.credentials)
    user_id = payload.get("sub")
    if not user_id:
        raise AppError(code="UNAUTHORIZED", message="Invalid token payload", status_code=401)

    db = get_db()
    repo = UsersRepository(db)
    user = await repo.find_by_id(user_id)
    if not user:
        raise AppError(code="UNAUTHORIZED", message="User not found", status_code=401)

    user["id"] = str(user["_id"])
    return user


async def get_current_user_id(user: dict[str, Any] = Depends(get_current_user)) -> str:
    return str(user["_id"])


async def require_verified_user(user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    if not user.get("is_verified"):
        raise AppError(code="EMAIL_NOT_VERIFIED", message="Email is not verified", status_code=403)
    return user


def hash_auth_code(identifier: str, code: str) -> str:
    msg = f"{identifier.lower().strip()}:{code}".encode("utf-8")
    key = settings.jwt_secret.encode("utf-8")
    return hmac.new(key, msg, hashlib.sha256).hexdigest()


def verify_auth_code(identifier: str, code: str, expected_hash: str) -> bool:
    computed = hash_auth_code(identifier, code)
    return hmac.compare_digest(computed, expected_hash)


def hash_verification_code(email: str, code: str) -> str:
    return hash_auth_code(email, code)


def verify_verification_code(email: str, code: str, expected_hash: str) -> bool:
    return verify_auth_code(email, code, expected_hash)
