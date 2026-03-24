from __future__ import annotations

from app.core.config import settings
from app.modules.calls.schemas import IceServer
from app.modules.webrtc.providers import TurnProvider, get_turn_providers


class WebRTCService:
    def __init__(self, providers: list[TurnProvider] | None = None) -> None:
        self._providers = providers

    async def get_ice_servers(self) -> list[IceServer]:
        servers = self._build_stun_servers()

        for provider in self._providers or get_turn_providers():
            try:
                provider_servers = await provider.get_ice_servers()
            except Exception:
                continue
            servers.extend(IceServer.model_validate(server) for server in provider_servers)

        return _dedupe_ice_servers(servers)

    def _build_stun_servers(self) -> list[IceServer]:
        if not settings.call_stun_urls:
            return []

        urls: str | list[str]
        urls = settings.call_stun_urls[0] if len(settings.call_stun_urls) == 1 else settings.call_stun_urls
        return [IceServer(urls=urls)]


def _dedupe_ice_servers(servers: list[IceServer]) -> list[IceServer]:
    deduped: list[IceServer] = []
    seen: set[tuple[tuple[str, ...], str | None, str | None]] = set()

    for server in servers:
        urls = (server.urls,) if isinstance(server.urls, str) else tuple(server.urls)
        key = (urls, server.username, server.credential)
        if key in seen:
            continue
        deduped.append(server)
        seen.add(key)

    return deduped
