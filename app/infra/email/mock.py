from __future__ import annotations

import logging
from app.infra.email.base import EmailSender

log = logging.getLogger("app.email")


class MockEmailSender(EmailSender):
    async def send_verification_code(self, *, to_email: str, code: str) -> None:
        log.info("[mock-email] to=%s code=%s", to_email, code)