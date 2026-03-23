from app.db.mongo import get_db
from app.modules.stickers.repository import StickersRepository
from app.modules.stickers.service import StickersService


def get_stickers_service() -> StickersService:
    return StickersService(StickersRepository(get_db()))
