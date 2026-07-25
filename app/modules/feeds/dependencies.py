from __future__ import annotations

from app.modules.auth.repository import UsersRepository
from app.modules.conversations.repository import ConversationsRepository
from app.modules.conversations.service import ConversationsService
from app.modules.feeds.service import FeedsService
from app.modules.messages.repository import MessagesRepository
from app.modules.messages.service import MessagesService
from app.modules.pings.repository import PingsRepository
from app.modules.pings.service import PingsService


def get_feeds_service() -> FeedsService:
    users_repo = UsersRepository()
    conversations_repo = ConversationsRepository()
    messages_repo = MessagesRepository()
    pings_service = PingsService(
        pings_repo=PingsRepository(),
        users_repo=users_repo,
        presence_service=None,
    )
    conversations_service = ConversationsService(
        repo=conversations_repo,
        pings_service=pings_service,
        users_repo=users_repo,
    )
    messages_service = MessagesService(
        repo=messages_repo,
        pings_service=pings_service,
        conversations_service=conversations_service,
    )

    return FeedsService(
        conversations_repo=conversations_repo,
        messages_service=messages_service,
        messages_repo=messages_repo,
        pings_service=pings_service,
        users_repo=users_repo,
    )
