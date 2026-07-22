from __future__ import annotations

from app.core.config import settings
from app.modules.notifications.repository import NotificationsRepository
from app.modules.notifications.service import NotificationsService


def get_notifications_service() -> NotificationsService:
    return NotificationsService(
        repo=NotificationsRepository(),
        push_queue_name=settings.push_queue_name,
    )
