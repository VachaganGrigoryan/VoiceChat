from __future__ import annotations

from app.core.config import settings

from app.modules.discovery.repository import DiscoveryTokensRepository
from app.modules.discovery.service import DiscoveryConfig, DiscoveryService

from app.modules.auth.repository import UsersRepository
from app.modules.relationships.dependencies import get_connection_service
from app.modules.realtime.presence import get_presence_backend


def get_discovery_service() -> DiscoveryService:
    users_repo = UsersRepository()
    tokens_repo = DiscoveryTokensRepository()

    presence = get_presence_backend()

    config = DiscoveryConfig(
        invite_base_url=settings.web_app_url,
        code_ttl_seconds=settings.discovery_code_ttl_seconds,
        default_link_ttl_seconds=settings.discovery_link_ttl_seconds,
    )

    return DiscoveryService(
        repo=tokens_repo,
        users_repo=users_repo,
        presence_service=presence,
        connection_service=get_connection_service(),
        config=config,
    )
