from __future__ import annotations

import secrets
from datetime import datetime, timedelta

from app.core.config import settings
from app.core.errors import AppError
from app.core.security import (
    create_access_token,
    generate_refresh_token,
    hash_refresh_token,
    hash_verification_code,
    verify_verification_code,
)
from app.modules.auth.refresh_repository import RefreshTokensRepository
from app.infra.email.jobs import SendVerificationCodeJob
from app.infra.queue import get_job_queue
from app.modules.auth.repository import UsersRepository
from app.modules.verification.repository import VerificationCodesRepository


PURPOSE_EMAIL_VERIFY = "email_verify"
PURPOSE_LOGIN = "login"

VERIFY_TTL_MINUTES = 10
LOGIN_TTL_MINUTES = 10
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

    async def _enqueue_verification_email(self, *, email: str, code: str) -> None:
        job = SendVerificationCodeJob(email=email, code=code)
        await self.job_queue.publish(
            queue_name=settings.email_queue_name,
            payload=job.model_dump(mode="json"),
        )

    async def _create_code_and_send_verification_email(self, *, user_id: str, email: str) -> None:
        code = _generate_6_digit_code()
        expires_at = datetime.utcnow() + timedelta(minutes=VERIFY_TTL_MINUTES)
        code_hash = hash_verification_code(email, code)

        await self.codes.create_code(
            user_id=user_id,
            email=email,
            purpose=PURPOSE_EMAIL_VERIFY,
            code_hash=code_hash,
            expires_at=expires_at,
        )

        await self._enqueue_verification_email(email=email, code=code)

    async def register(self, *, email: str) -> dict:
        """
        Create user if needed, and always send an email verification code
        unless already verified (then still OK to resend verify code, but not necessary).
        """
        user = await self.users.create_if_not_exists(email)

        # Anti-enumeration friendly response
        generic_response = {
            "email": email,
            "message": "If the email can be used, a verification code has been sent."
        }

        if user.get("is_verified"):
            # You can choose:
            # - either return success without sending code
            # - or send a login code instead
            # Requirement says register sends verification code, but user is already verified,
            # so we keep it simple: send a login code is NOT requested here.
            return generic_response

        await self._create_code_and_send_verification_email(user_id=str(user["_id"]), email=email)

        return generic_response

    async def login(self, *, email: str) -> dict:
        """
        Send login code only if user exists and is verified.
        (To avoid user enumeration, you may always return 200.)
        """
        generic_response = {
            "email": email,
            "message": "If the account exists and is verified, a login code has been sent."
        }

        user = await self.users.find_by_email(email)
        # Anti-enumeration option: always return 200 and do nothing if user not found.
        if not user:
            return generic_response

        if not user.get("is_verified"):
            return generic_response

        await self._create_code_and_send_verification_email(user_id=str(user["_id"]), email=email)

        return generic_response

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
        expires_at = datetime.utcnow() + timedelta(days=settings.refresh_token_expire_days)

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

    async def verify(self, *, email: str, code: str) -> dict:
        """
        Unified verify endpoint (supports 3-endpoint design):
        - If user is NOT verified: accept only PURPOSE_EMAIL_VERIFY
        - If user IS verified: accept PURPOSE_LOGIN (and optionally PURPOSE_EMAIL_VERIFY as fallback)
        On success returns JWT.
        """
        user = await self.users.find_by_email(email)
        if not user:
            # Do not leak existence
            raise AppError(code="CODE_INVALID", message="Verification code is invalid or expired", status_code=400)

        user_id = str(user["_id"])
        is_verified = bool(user.get("is_verified"))

        # Decide which purposes are acceptable
        allowed_purposes = [PURPOSE_LOGIN] if is_verified else [PURPOSE_EMAIL_VERIFY]

        # Optional: allow verify code even if already verified (harmless, can help UX)
        if is_verified:
            allowed_purposes.append(PURPOSE_EMAIL_VERIFY)

        code_doc = await self.codes.find_active_by_email_any(email=email, purposes=allowed_purposes)
        if not code_doc:
            raise AppError(code="CODE_INVALID", message="Verification code is invalid or expired", status_code=400)

        # Increment attempts on the specific code doc (brute force protection)
        attempts = await self.codes.increment_attempts(str(code_doc["_id"]))
        if attempts > MAX_ATTEMPTS:
            await self.codes.delete_by_user_and_purpose(user_id=code_doc["user_id"], purpose=code_doc["purpose"])
            raise AppError(
                code="TOO_MANY_ATTEMPTS",
                message="Too many attempts. Please request a new code.",
                status_code=429,
            )

        if not verify_verification_code(email, code, code_doc["code_hash"]):
            raise AppError(code="CODE_INVALID", message="Verification code is invalid", status_code=400)

        # If this was an email verification code, mark verified
        if code_doc["purpose"] == PURPOSE_EMAIL_VERIFY and not is_verified:
            await self.users.set_verified(user_id)

        # Consume codes so they can't be reused (replay protection)
        # - delete that purpose (or all purposes if you want stricter)
        await self.codes.delete_by_user_and_purpose(user_id=user_id, purpose=code_doc["purpose"])

        return await self._issue_token_pair(user_id=user_id)

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
        expires_at = datetime.utcnow() + timedelta(days=settings.refresh_token_expire_days)

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