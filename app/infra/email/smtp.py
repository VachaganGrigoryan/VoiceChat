from __future__ import annotations

from email.message import EmailMessage

import aiosmtplib

from app.core.config import settings
from app.core.errors import AppError
from app.infra.email.base import EmailSender


class SmtpEmailSender(EmailSender):
    async def send_verification_code(self, *, to_email: str, code: str) -> None:
        msg = EmailMessage()
        msg["From"] = "no-reply@voicechat.local"
        msg["To"] = to_email
        msg["Subject"] = "Your verification code"
        msg.set_content(f"Your verification code is: {code}\n\nIt expires soon.")

        try:
            await aiosmtplib.send(
                msg,
                hostname=settings.smtp_host,
                port=settings.smtp_port,
                username=settings.smtp_user or None,
                password=settings.smtp_pass or None,
                timeout=10,
            )
        except Exception as e:
            raise AppError(code="EMAIL_SEND_FAILED", message="Failed to send verification email", status_code=502, details=str(e))