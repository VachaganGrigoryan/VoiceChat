from app.modules.messages.repository import MessagesRepository
from app.modules.messages.service import MessagesService
from app.modules.pings.repository import PingsRepository
from app.modules.pings.service import PingsService
from app.modules.auth.repository import UsersRepository
from app.modules.conversations.repository import ConversationsRepository
from app.modules.conversations.service import ConversationsService


def get_messages_service() -> MessagesService:
    pings_service = PingsService(
        pings_repo=PingsRepository(),
        users_repo=UsersRepository(),
        presence_service=None,
    )
    conversations_service = ConversationsService(
        repo=ConversationsRepository(),
        pings_service=pings_service,
    )

    return MessagesService(
        repo=MessagesRepository(),
        pings_service=pings_service,
        conversations_service=conversations_service,
    )
