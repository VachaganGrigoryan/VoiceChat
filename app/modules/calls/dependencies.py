from __future__ import annotations

from app.db.mongo import get_db
from app.modules.auth.repository import UsersRepository
from app.modules.calls.repository import CallsRepository
from app.modules.calls.service import CallsService
from app.modules.messages.repository import MessagesRepository
from app.modules.pings.repository import PingsRepository
from app.modules.pings.service import PingsService
from app.modules.realtime.presence import get_presence_backend
from app.modules.webrtc.dependencies import get_webrtc_service


def get_calls_service() -> CallsService:
    db = get_db()

    users_repo = UsersRepository(db)
    presence = get_presence_backend()
    pings_service = PingsService(
        pings_repo=PingsRepository(db),
        users_repo=users_repo,
        presence_service=presence,
    )

    return CallsService(
        repo=CallsRepository(db),
        users_repo=users_repo,
        pings_service=pings_service,
        presence_service=presence,
        webrtc_service=get_webrtc_service(),
        messages_repo=MessagesRepository(db),
    )
