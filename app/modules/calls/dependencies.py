from __future__ import annotations

from app.modules.auth.repository import UsersRepository
from app.modules.calls.repository import CallsRepository
from app.modules.calls.service import CallsService
from app.modules.conversations.repository import ConversationsRepository
from app.modules.conversations.service import ConversationsService
from app.modules.messages.repository import MessagesRepository
from app.modules.relationships.dependencies import get_connection_service
from app.modules.realtime.presence import get_presence_backend
from app.modules.webrtc.dependencies import get_webrtc_service


def get_calls_service() -> CallsService:
    users_repo = UsersRepository()
    presence = get_presence_backend()
    connection_service = get_connection_service()
    conversations_service = ConversationsService(
        repo=ConversationsRepository(),
        connection_service=connection_service,
    )

    return CallsService(
        repo=CallsRepository(),
        users_repo=users_repo,
        connection_service=connection_service,
        presence_service=presence,
        webrtc_service=get_webrtc_service(),
        messages_repo=MessagesRepository(),
        conversations_service=conversations_service,
    )
