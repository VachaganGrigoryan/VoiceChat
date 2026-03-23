from app.db.mongo import get_db
from app.modules.messages.repository import MessagesRepository
from app.modules.messages.service import MessagesService
from app.modules.pings.repository import PingsRepository
from app.modules.pings.service import PingsService
from app.modules.auth.repository import UsersRepository
from app.modules.stickers.repository import StickersRepository
from app.modules.stickers.service import StickersService


def get_messages_service() -> MessagesService:
    db = get_db()

    pings_service = PingsService(
        pings_repo=PingsRepository(db),
        users_repo=UsersRepository(db),
        presence_service=None,
    )

    return MessagesService(
        repo=MessagesRepository(db),
        pings_service=pings_service,
        sticker_service=StickersService(StickersRepository(db)),
    )
