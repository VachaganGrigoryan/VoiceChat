from __future__ import annotations

from app.core.config import settings
from app.db.mongo import get_db

from app.modules.discovery.repository import DiscoveryTokensRepository
from app.modules.discovery.service import DiscoveryConfig, DiscoveryService

from app.modules.auth.repository import UsersRepository
from app.modules.pings.repository import PingsRepository
from app.modules.pings.service import PingsService
from app.modules.realtime.presence import get_presence_backend


def get_discovery_service() -> DiscoveryService:
    db = get_db()

    users_repo = UsersRepository(db)
    tokens_repo = DiscoveryTokensRepository(db)

    presence = get_presence_backend()

    pings_service = PingsService(
        pings_repo=PingsRepository(db),
        users_repo=users_repo,
        presence_service=None,
    )

    config = DiscoveryConfig(
        invite_base_url=settings.web_app_url,
        code_ttl_seconds=settings.discovery_code_ttl_seconds,
        default_link_ttl_seconds=settings.discovery_link_ttl_seconds,
    )

    return DiscoveryService(
        repo=tokens_repo,
        users_repo=users_repo,
        presence_service=presence,
        pings_service=pings_service,
        config=config,
    )