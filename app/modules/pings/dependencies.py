from __future__ import annotations

from app.modules.auth.repository import UsersRepository
from app.modules.conversations.repository import ConversationsRepository
from app.modules.notifications.dependencies import get_notifications_service
from app.modules.pings.repository import PingsRepository
from app.modules.pings.service import PingsService
from app.modules.realtime.presence import get_presence_backend


def get_pings_service() -> PingsService:
    users_repo = UsersRepository()
    pings_repo = PingsRepository()

    presence = get_presence_backend()
    return PingsService(
        pings_repo=pings_repo,
        users_repo=users_repo,
        presence_service=presence,
        conversations_repo=ConversationsRepository(),
        notifications_service=get_notifications_service(),
    )
