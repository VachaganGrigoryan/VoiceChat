from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.core.config import settings
from app.modules.calls.schemas import IceServer
from app.modules.webrtc.providers import (
    CloudflareProvider,
    clear_turn_provider_cache,
    get_turn_providers,
)
from app.modules.webrtc.service import WebRTCService


class _Provider:
    def __init__(self, servers=None, *, error: Exception | None = None) -> None:
        self.servers = servers or []
        self.error = error

    async def get_ice_servers(self):
        if self.error is not None:
            raise self.error
        return self.servers


@pytest.fixture(autouse=True)
def clear_provider_cache():
    clear_turn_provider_cache()
    yield
    clear_turn_provider_cache()


def test_get_turn_providers_uses_primary_and_secondary_order(monkeypatch):
    monkeypatch.setattr(settings, "turn_provider", "cloudflare")
    monkeypatch.setattr(settings, "turn_multi", True)

    providers = get_turn_providers()

    assert [provider.name for provider in providers] == ["cloudflare", "coturn"]


@pytest.mark.asyncio
async def test_webrtc_service_includes_stun_and_falls_back_to_secondary(monkeypatch):
    monkeypatch.setattr(settings, "call_stun_urls", ["stun:stun.example.com:19302"])
    service = WebRTCService(
        providers=[
            _Provider(error=RuntimeError("primary failed")),
            _Provider(
                servers=[
                    {
                        "urls": ["turn:backup.example.com:3478"],
                        "username": "backup-user",
                        "credential": "backup-pass",
                    }
                ]
            ),
        ]
    )

    servers = await service.get_ice_servers()

    assert len(servers) == 2
    assert servers[0].urls == "stun:stun.example.com:19302"
    assert servers[1].username == "backup-user"


@pytest.mark.asyncio
async def test_cloudflare_provider_pauses_when_usage_threshold_is_reached(monkeypatch):
    monkeypatch.setattr(settings, "cf_turn_key_id", "key-1")
    monkeypatch.setattr(settings, "cf_turn_api_token", "token-1")
    monkeypatch.setattr(settings, "cf_account_id", "account-1")
    monkeypatch.setattr(settings, "cf_turn_pause_at_gb", 999.0)

    provider = CloudflareProvider()
    monkeypatch.setattr(provider, "_get_usage_egress_gb", AsyncMock(return_value=999.2))
    fetch_dynamic = AsyncMock()
    monkeypatch.setattr(provider, "_fetch_dynamic_ice_servers", fetch_dynamic)

    servers = await provider.get_ice_servers()

    assert servers == []
    fetch_dynamic.assert_not_awaited()


@pytest.mark.asyncio
async def test_cloudflare_provider_returns_dynamic_servers_below_threshold(monkeypatch):
    monkeypatch.setattr(settings, "cf_turn_key_id", "key-1")
    monkeypatch.setattr(settings, "cf_turn_api_token", "token-1")
    monkeypatch.setattr(settings, "cf_account_id", "account-1")
    monkeypatch.setattr(settings, "cf_turn_pause_at_gb", 999.0)

    provider = CloudflareProvider()
    monkeypatch.setattr(provider, "_get_usage_egress_gb", AsyncMock(return_value=120.0))
    fetch_dynamic = AsyncMock(
        return_value=[
            IceServer(
                urls=["turn:cloudflare.example.com:3478"],
                username="cf-user",
                credential="cf-pass",
            )
        ]
    )
    monkeypatch.setattr(provider, "_fetch_dynamic_ice_servers", fetch_dynamic)

    servers = await provider.get_ice_servers()

    assert len(servers) == 1
    assert servers[0].username == "cf-user"
    fetch_dynamic.assert_awaited_once()
