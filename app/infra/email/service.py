from __future__ import annotations

from app.infra.email import get_email_sender


class EmailService:
    async def send_verification_code(self, *, email: str, code: str) -> None:
        sender = get_email_sender()
        await sender.send_verification_code(to_email=email, code=code)
