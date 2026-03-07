from __future__ import annotations

from dataclasses import dataclass

from limits import parse
from limits.aio import storage, strategies

from app.core.config import settings
from app.core.exceptions import AppError


def build_storage(storage_uri: str):
    if storage_uri == "async+memory://":
        return storage.MemoryStorage()
    if storage_uri.startswith("async+redis://"):
        return storage.RedisStorage(storage_uri, implementation="redispy")

    raise ValueError(f"Unsupported rate limit storage URI: {storage_uri}")


@dataclass(frozen=True)
class RateLimitRule:
    value: str


class RateLimiterService:
    def __init__(self, storage_uri: str):
        self.storage_uri = storage_uri
        self.storage = build_storage(storage_uri)
        self.limiter = strategies.FixedWindowRateLimiter(self.storage)

    async def hit(self, *, key: str, rule: RateLimitRule) -> None:
        item = parse(rule.value)
        allowed = await self.limiter.hit(item, key)

        if not allowed:
            raise AppError(
                code="RATE_LIMITED",
                message="Too many requests. Please try again later.",
                status_code=429,
            )

    async def get_window_stats(self, *, key: str, rule: RateLimitRule):
        item = parse(rule.value)
        return await self.limiter.get_window_stats(item, key)


rate_limiter = RateLimiterService(settings.rate_limit_storage_uri)