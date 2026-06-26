from app.modules.messages.repository import MessagesRepository
from app.modules.messages.service import MessagesService
from app.modules.pings.repository import PingsRepository
from app.modules.pings.service import PingsService
from app.modules.auth.repository import UsersRepository


def get_messages_service() -> MessagesService:
    pings_service = PingsService(
        pings_repo=PingsRepository(),
        users_repo=UsersRepository(),
        presence_service=None,
    )

    return MessagesService(
        repo=MessagesRepository(),
        pings_service=pings_service,
    )