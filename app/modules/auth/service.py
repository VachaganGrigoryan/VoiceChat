from __future__ import annotations

import secrets
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, UTC

from app.core.config import settings
from app.core.errors import AppError
from app.core.security import (
    create_access_token,
    generate_refresh_token,
    hash_auth_code,
    hash_refresh_token,
    verify_auth_code,
)
from app.modules.auth.refresh_repository import RefreshTokensRepository
from app.infra.email.jobs import SendVerificationCodeJob
from app.infra.queue import get_job_queue
from app.modules.auth.methods import (
    AUTH_METHOD_EMAIL,
    AuthMethodHandler,
    EmailAuthMethodHandler,
)
from app.modules.auth.repository import UsersRepository
from app.modules.verification.repository import VerificationCodesRepository


AUTH_CHALLENGE_PURPOSE = "auth"
AUTH_CODE_TTL_MINUTES = 10
MAX_ATTEMPTS = 5


def _generate_6_digit_code() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


class AuthService:
    def __init__(
        self,
        users: UsersRepository,
        codes: VerificationCodesRepository,
        refresh_tokens: RefreshTokensRepository,
    ):
        self.users = users
        self.codes = codes
        self.refresh_tokens = refresh_tokens
        self.job_queue = get_job_queue()
        self.handlers: dict[str, AuthMethodHandler] = {
            AUTH_METHOD_EMAIL: EmailAuthMethodHandler(
                users=users,
                send_code=self._enqueue_verification_email,
            )
        }

    async def _enqueue_verification_email(self, *, email: str, code: str) -> None:
        job = SendVerificationCodeJob(email=email, code=code)
        await self.job_queue.publish(
            queue_name=settings.email_queue_name,
            payload=job.model_dump(mode="json"),
        )

    async def _create_auth_code(
        self,
        *,
        method: str,
        identifier: str,
        user_id: str,
        deliver_code: Callable[..., Awaitable[None]],
    ) -> None:
        code = _generate_6_digit_code()
        expires_at = datetime.now(UTC) + timedelta(minutes=AUTH_CODE_TTL_MINUTES)
        code_hash = hash_auth_code(identifier, code)

        await self.codes.create_code(
            method=method,
            identifier=identifier,
            user_id=user_id,
            purpose=AUTH_CHALLENGE_PURPOSE,
            code_hash=code_hash,
            expires_at=expires_at,
        )

        await deliver_code(identifier=identifier, code=code)

    def _get_handler(self, *, method: str) -> AuthMethodHandler:
        handler = self.handlers.get(method)
        if not handler:
            raise AppError(
                code="UNSUPPORTED_AUTH_METHOD",
                message="Unsupported auth method",
                status_code=400,
            )
        return handler

    async def start_auth(self, *, method: str, identifier: str) -> dict:
        handler = self._get_handler(method=method)
        start = await handler.prepare_start(identifier=identifier)
        await self._create_auth_code(
            method=start.method,
            identifier=start.identifier,
            user_id=start.user_id,
            deliver_code=handler.deliver_code,
        )
        return {
            "method": start.method,
            "identifier": start.identifier,
            "message": start.message,
        }

    async def issue_token_pair_for_user(
        self,
        *,
        user_id: str,
        user_agent: str | None = None,
        ip: str | None = None,
    ) -> dict:
        return await self._issue_token_pair(
            user_id=user_id,
            user_agent=user_agent,
            ip=ip,
        )

    async def _issue_token_pair(
        self,
        *,
        user_id: str,
        user_agent: str | None = None,
        ip: str | None = None,
    ) -> dict:
        access_token = create_access_token(subject=user_id)

        refresh_token = generate_refresh_token()
        refresh_hash = hash_refresh_token(refresh_token)
        expires_at = datetime.now(UTC) + timedelta(days=settings.refresh_token_expire_days)

        await self.refresh_tokens.create_token(
            user_id=user_id,
            token_hash=refresh_hash,
            expires_at=expires_at,
            user_agent=user_agent,
            ip=ip,
        )

        return {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "token_type": "bearer",
        }

    async def finish_auth(self, *, method: str, identifier: str, code: str) -> dict:
        handler = self._get_handler(method=method)
        finish = await handler.prepare_finish(identifier=identifier)
        if not finish:
            raise AppError(
                code="CODE_INVALID",
                message="Verification code is invalid or expired",
                status_code=400,
            )

        code_doc = await self.codes.find_active_by_identifier_any(
            method=finish.method,
            identifier=finish.identifier,
            purposes=[AUTH_CHALLENGE_PURPOSE],
        )
        if not code_doc:
            raise AppError(code="CODE_INVALID", message="Verification code is invalid or expired", status_code=400)

        attempts = await self.codes.increment_attempts(str(code_doc["_id"]))
        if attempts > MAX_ATTEMPTS:
            await self.codes.delete_by_user_method_and_purpose(
                user_id=finish.user_id,
                method=finish.method,
                purpose=AUTH_CHALLENGE_PURPOSE,
            )
            raise AppError(
                code="TOO_MANY_ATTEMPTS",
                message="Too many attempts. Please request a new code.",
                status_code=429,
            )

        if not verify_auth_code(finish.identifier, code, code_doc["code_hash"]):
            raise AppError(code="CODE_INVALID", message="Verification code is invalid", status_code=400)

        await handler.on_success(finish=finish)
        await self.codes.delete_by_user_method_and_purpose(
            user_id=finish.user_id,
            method=finish.method,
            purpose=AUTH_CHALLENGE_PURPOSE,
        )

        return await self._issue_token_pair(user_id=finish.user_id)

    async def refresh(
        self,
        *,
        refresh_token: str,
        user_agent: str | None = None,
        ip: str | None = None,
    ) -> dict:
        token_hash = hash_refresh_token(refresh_token)

        existing = await self.refresh_tokens.find_any_by_hash(token_hash=token_hash)
        if not existing:
            raise AppError(code="UNAUTHORIZED", message="Invalid refresh token", status_code=401)

        # Reuse detection: token exists but is no longer active
        active = await self.refresh_tokens.find_active_by_hash(token_hash=token_hash)
        if not active:
            user_id = str(existing["user_id"])
            await self.refresh_tokens.revoke_all_for_user(user_id=user_id)
            raise AppError(
                code="UNAUTHORIZED",
                message="Refresh token reuse detected. Please login again.",
                status_code=401,
            )

        user_id = str(active["user_id"])

        new_refresh_token = generate_refresh_token()
        new_refresh_hash = hash_refresh_token(new_refresh_token)

        await self.refresh_tokens.revoke_token(
            token_hash=token_hash,
            replaced_by_token_hash=new_refresh_hash,
        )

        access_token = create_access_token(subject=user_id)
        expires_at = datetime.now(UTC) + timedelta(days=settings.refresh_token_expire_days)

        await self.refresh_tokens.create_token(
            user_id=user_id,
            token_hash=new_refresh_hash,
            expires_at=expires_at,
            user_agent=user_agent,
            ip=ip,
        )

        return {
            "access_token": access_token,
            "refresh_token": new_refresh_token,
            "token_type": "bearer",
        }

    async def logout(self, *, refresh_token: str) -> dict:
        token_hash = hash_refresh_token(refresh_token)
        await self.refresh_tokens.revoke_token(token_hash=token_hash)
        return {"message": "Logged out successfully"}
