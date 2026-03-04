from __future__ import annotations

from app.core.config import settings
from app.infra.email.base import EmailSender
from app.infra.email.mock import MockEmailSender
from app.infra.email.smtp import SmtpEmailSender


def get_email_sender() -> EmailSender:
    if settings.email_provider == "smtp":
        return SmtpEmailSender()
    return MockEmailSender()