from __future__ import annotations

from redis.asyncio import Redis

from app.core.config import settings
from app.modules.realtime.presence.base import PresenceBackend
from app.modules.realtime.presence.memory import InMemoryPresenceBackend
from app.modules.realtime.presence.redis import RedisPresenceBackend

_presence_backend: PresenceBackend | None = None
_redis_client: Redis | None = None


def get_redis_client() -> Redis:
    global _redis_client

    if _redis_client is None:
        _redis_client = Redis.from_url(
            settings.redis_url,
            encoding="utf-8",
            decode_responses=False,
        )

    return _redis_client


def get_presence_backend() -> PresenceBackend:
    global _presence_backend

    if _presence_backend is not None:
        return _presence_backend

    if settings.presence_backend == "redis":
        _presence_backend = RedisPresenceBackend(
            redis=get_redis_client(),
            key_prefix=settings.presence_key_prefix,
        )
    else:
        _presence_backend = InMemoryPresenceBackend()

    return _presence_backend


async def close_presence_backend() -> None:
    global _presence_backend, _redis_client

    _presence_backend = None

    if _redis_client is not None:
        await _redis_client.aclose()
        _redis_client = None
