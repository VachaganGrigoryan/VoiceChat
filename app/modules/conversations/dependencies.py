from __future__ import annotations

from app.modules.auth.repository import UsersRepository
from app.modules.conversations.repository import ConversationsRepository
from app.modules.conversations.service import ConversationsService
from app.modules.relationships.dependencies import get_connection_service
from app.modules.realtime.presence import get_presence_backend


def get_conversations_service() -> ConversationsService:
    users_repo = UsersRepository()
    presence = get_presence_backend()
    connection_service = get_connection_service()

    return ConversationsService(
        repo=ConversationsRepository(),
        connection_service=connection_service,
        users_repo=users_repo,
        presence_service=presence,
    )
