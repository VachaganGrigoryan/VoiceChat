from __future__ import annotations

import secrets
from datetime import datetime, timedelta

from app.core.exceptions import AppError
from app.core.security import create_access_token, hash_verification_code, verify_verification_code
from app.infra.email import get_email_sender
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
    def __init__(self, users: UsersRepository, codes: VerificationCodesRepository):
        self.users = users
        self.codes = codes

    async def register(self, *, email: str) -> dict:
        """
        Create user if needed, and always send an email verification code
        unless already verified (then still OK to resend verify code, but not necessary).
        """
        user = await self.users.create_if_not_exists(email)

        if user.get("is_verified"):
            # You can choose:
            # - either return success without sending code
            # - or send a login code instead
            # Requirement says register sends verification code, but user is already verified,
            # so we keep it simple: send a login code is NOT requested here.
            return {"email": user["email"], "verification_sent": False}

        code = _generate_6_digit_code()
        expires_at = datetime.utcnow() + timedelta(minutes=VERIFY_TTL_MINUTES)
        code_hash = hash_verification_code(email, code)

        await self.codes.create_code(
            user_id=str(user["_id"]),
            email=email,
            purpose=PURPOSE_EMAIL_VERIFY,
            code_hash=code_hash,
            expires_at=expires_at,
        )

        sender = get_email_sender()
        await sender.send_verification_code(to_email=email, code=code)

        return {"email": user["email"], "verification_sent": True}

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

        token = create_access_token(subject=user_id)
        return {"access_token": token, "token_type": "bearer"}

    async def login(self, *, email: str) -> dict:
        """
        Send login code only if user exists and is verified.
        (To avoid user enumeration, you may always return 200.)
        """
        user = await self.users.find_by_email(email)
        # Anti-enumeration option: always return 200 and do nothing if user not found.
        if not user:
            return {"email": email.lower().strip(), "login_code_sent": True}

        if not user.get("is_verified"):
            raise AppError(code="EMAIL_NOT_VERIFIED", message="Email is not verified", status_code=403)

        code = _generate_6_digit_code()
        expires_at = datetime.utcnow() + timedelta(minutes=LOGIN_TTL_MINUTES)
        code_hash = hash_verification_code(email, code)

        await self.codes.create_code(
            user_id=str(user["_id"]),
            email=email,
            purpose=PURPOSE_LOGIN,
            code_hash=code_hash,
            expires_at=expires_at,
        )

        sender = get_email_sender()
        # reuse same email method for now
        await sender.send_verification_code(to_email=email, code=code)

        return {"email": user["email"], "login_code_sent": True}