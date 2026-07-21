from __future__ import annotations

from app.modules.auth.repository import UsersRepository
from app.modules.conversations.repository import ConversationsRepository
from app.modules.conversations.service import ConversationsService
from app.modules.pings.repository import PingsRepository
from app.modules.pings.service import PingsService
from app.modules.realtime.presence import get_presence_backend


def get_conversations_service() -> ConversationsService:
    users_repo = UsersRepository()
    presence = get_presence_backend()
    pings_service = PingsService(
        pings_repo=PingsRepository(),
        users_repo=users_repo,
        presence_service=presence,
    )

    return ConversationsService(
        repo=ConversationsRepository(),
        pings_service=pings_service,
        users_repo=users_repo,
        presence_service=presence,
    )
