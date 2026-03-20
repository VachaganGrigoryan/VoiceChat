from __future__ import annotations

from app.db.mongo import get_db

from app.modules.auth.repository import UsersRepository
from app.modules.pings.repository import PingsRepository
from app.modules.pings.service import PingsService
from app.modules.realtime.presence import get_presence_backend


def get_pings_service() -> PingsService:
    db = get_db()

    users_repo = UsersRepository(db)
    pings_repo = PingsRepository(db)

    presence = get_presence_backend()
    return PingsService(
        pings_repo=pings_repo,
        users_repo=users_repo,
        presence_service=presence,
    )