from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.modules.realtime.presence import factory


@pytest.mark.asyncio
async def test_close_presence_backend_clears_cached_backend_and_client(monkeypatch):
    fake_client = AsyncMock()
    fake_backend = object()

    monkeypatch.setattr(factory, "_redis_client", fake_client)
    monkeypatch.setattr(factory, "_presence_backend", fake_backend)

    await factory.close_presence_backend()

    fake_client.aclose.assert_awaited_once()
    assert factory._redis_client is None
    assert factory._presence_backend is None


@pytest.mark.asyncio
async def test_close_presence_backend_without_client_still_clears_backend(monkeypatch):
    fake_backend = object()

    monkeypatch.setattr(factory, "_redis_client", None)
    monkeypatch.setattr(factory, "_presence_backend", fake_backend)

    await factory.close_presence_backend()

    assert factory._redis_client is None
    assert factory._presence_backend is None
