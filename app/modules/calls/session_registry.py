from __future__ import annotations

from abc import ABC, abstractmethod
from collections import defaultdict
from dataclasses import dataclass

from redis.asyncio import Redis

from app.core.config import settings

_REGISTRY_TTL_SECONDS = 60 * 60 * 24


@dataclass(frozen=True)
class CallSocketUnbindResult:
    call_id: str
    user_id: str
    remaining_connection_count: int


class CallSessionRegistry(ABC):
    @abstractmethod
    async def bind_socket(self, *, call_id: str, user_id: str, sid: str) -> None:
        raise NotImplementedError

    @abstractmethod
    async def unbind_sid(self, sid: str) -> list[CallSocketUnbindResult]:
        raise NotImplementedError

    @abstractmethod
    async def get_connection_count(self, *, call_id: str, user_id: str) -> int:
        raise NotImplementedError

    @abstractmethod
    async def clear_call(self, *, call_id: str, participant_user_ids: list[str]) -> None:
        raise NotImplementedError

    @abstractmethod
    async def close(self) -> None:
        raise NotImplementedError


class InMemoryCallSessionRegistry(CallSessionRegistry):
    def __init__(self) -> None:
        self._call_user_sids: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
        self._sid_bindings: dict[str, set[tuple[str, str]]] = defaultdict(set)

    async def bind_socket(self, *, call_id: str, user_id: str, sid: str) -> None:
        self._call_user_sids[call_id][user_id].add(sid)
        self._sid_bindings[sid].add((call_id, user_id))

    async def unbind_sid(self, sid: str) -> list[CallSocketUnbindResult]:
        bindings = list(self._sid_bindings.pop(sid, set()))
        results: list[CallSocketUnbindResult] = []

        for call_id, user_id in bindings:
            user_bindings = self._call_user_sids.get(call_id, {}).get(user_id)
            if not user_bindings:
                continue

            user_bindings.discard(sid)
            remaining = len(user_bindings)
            if remaining == 0:
                self._call_user_sids.get(call_id, {}).pop(user_id, None)
            if not self._call_user_sids.get(call_id):
                self._call_user_sids.pop(call_id, None)

            results.append(
                CallSocketUnbindResult(
                    call_id=call_id,
                    user_id=user_id,
                    remaining_connection_count=remaining,
                )
            )

        return results

    async def get_connection_count(self, *, call_id: str, user_id: str) -> int:
        return len(self._call_user_sids.get(call_id, {}).get(user_id, set()))

    async def clear_call(self, *, call_id: str, participant_user_ids: list[str]) -> None:
        user_bindings = self._call_user_sids.pop(call_id, {})
        for user_id in participant_user_ids:
            for sid in user_bindings.get(user_id, set()):
                sid_bindings = self._sid_bindings.get(sid)
                if sid_bindings is None:
                    continue

                sid_bindings.discard((call_id, user_id))
                if not sid_bindings:
                    self._sid_bindings.pop(sid, None)

    async def close(self) -> None:
        self._call_user_sids.clear()
        self._sid_bindings.clear()


class RedisCallSessionRegistry(CallSessionRegistry):
    def __init__(self, redis: Redis, *, key_prefix: str = "call_sessions") -> None:
        self.redis = redis
        self.key_prefix = key_prefix

    def _call_user_key(self, *, call_id: str, user_id: str) -> str:
        return f"{self.key_prefix}:call:{call_id}:user:{user_id}:sids"

    def _sid_key(self, sid: str) -> str:
        return f"{self.key_prefix}:sid:{sid}:bindings"

    def _binding_value(self, *, call_id: str, user_id: str) -> str:
        return f"{call_id}:{user_id}"

    def _parse_binding_value(self, value: str) -> tuple[str, str]:
        call_id, user_id = value.split(":", 1)
        return call_id, user_id

    async def bind_socket(self, *, call_id: str, user_id: str, sid: str) -> None:
        user_key = self._call_user_key(call_id=call_id, user_id=user_id)
        sid_key = self._sid_key(sid)
        binding_value = self._binding_value(call_id=call_id, user_id=user_id)

        async with self.redis.pipeline(transaction=True) as pipe:
            pipe.sadd(user_key, sid)
            pipe.expire(user_key, _REGISTRY_TTL_SECONDS)
            pipe.sadd(sid_key, binding_value)
            pipe.expire(sid_key, _REGISTRY_TTL_SECONDS)
            await pipe.execute()

    async def unbind_sid(self, sid: str) -> list[CallSocketUnbindResult]:
        sid_key = self._sid_key(sid)
        binding_values = await self.redis.smembers(sid_key)
        if not binding_values:
            await self.redis.delete(sid_key)
            return []

        await self.redis.delete(sid_key)

        results: list[CallSocketUnbindResult] = []
        for raw_value in binding_values:
            value = raw_value.decode("utf-8") if isinstance(raw_value, bytes) else str(raw_value)
            call_id, user_id = self._parse_binding_value(value)
            user_key = self._call_user_key(call_id=call_id, user_id=user_id)

            async with self.redis.pipeline(transaction=True) as pipe:
                pipe.srem(user_key, sid)
                pipe.scard(user_key)
                counts = await pipe.execute()

            remaining = int(counts[1] or 0)
            if remaining <= 0:
                await self.redis.delete(user_key)

            results.append(
                CallSocketUnbindResult(
                    call_id=call_id,
                    user_id=user_id,
                    remaining_connection_count=max(remaining, 0),
                )
            )

        return results

    async def get_connection_count(self, *, call_id: str, user_id: str) -> int:
        return int(await self.redis.scard(self._call_user_key(call_id=call_id, user_id=user_id)))

    async def clear_call(self, *, call_id: str, participant_user_ids: list[str]) -> None:
        for user_id in participant_user_ids:
            binding_value = self._binding_value(call_id=call_id, user_id=user_id)
            user_key = self._call_user_key(call_id=call_id, user_id=user_id)
            sids = await self.redis.smembers(user_key)

            for raw_sid in sids:
                sid = raw_sid.decode("utf-8") if isinstance(raw_sid, bytes) else str(raw_sid)
                sid_key = self._sid_key(sid)
                await self.redis.srem(sid_key, binding_value)
                if int(await self.redis.scard(sid_key)) <= 0:
                    await self.redis.delete(sid_key)

            await self.redis.delete(user_key)

    async def close(self) -> None:
        return None


_registry: CallSessionRegistry | None = None
_redis_client: Redis | None = None


def get_call_session_registry() -> CallSessionRegistry:
    global _registry, _redis_client

    if _registry is not None:
        return _registry

    if settings.call_session_backend == "redis":
        if _redis_client is None:
            _redis_client = Redis.from_url(
                settings.redis_url,
                encoding="utf-8",
                decode_responses=False,
            )
        _registry = RedisCallSessionRegistry(
            _redis_client,
            key_prefix=settings.call_session_key_prefix,
        )
    else:
        _registry = InMemoryCallSessionRegistry()

    return _registry


async def close_call_session_registry() -> None:
    global _registry, _redis_client

    if _registry is not None:
        await _registry.close()
        _registry = None

    if _redis_client is not None:
        await _redis_client.aclose()
        _redis_client = None
