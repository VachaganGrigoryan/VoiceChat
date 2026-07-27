from app.modules.messages.repository import MessagesRepository
from app.modules.messages.service import MessagesService
from app.modules.channels.repository import ChannelsRepository
from app.modules.auth.repository import UsersRepository
from app.modules.conversations.repository import ConversationsRepository
from app.modules.conversations.service import ConversationsService
from app.modules.relationships.dependencies import get_connection_service


def get_messages_service() -> MessagesService:
    users_repo = UsersRepository()
    conversations_service = ConversationsService(
        repo=ConversationsRepository(),
        connection_service=get_connection_service(),
        users_repo=users_repo,
    )

    return MessagesService(
        repo=MessagesRepository(),
        conversations_service=conversations_service,
        channels_repo=ChannelsRepository(),
    )
