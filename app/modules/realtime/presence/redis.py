from __future__ import annotations

from typing import cast

from redis.asyncio import Redis

from app.modules.realtime.presence.base import PresenceBackend, PresenceState


class RedisPresenceBackend(PresenceBackend):
    def __init__(self, redis: Redis, key_prefix: str = "presence") -> None:
        self.redis = redis
        self.key_prefix = key_prefix

    def _user_connections_key(self, user_id: str) -> str:
        return f"{self.key_prefix}:user:{user_id}:connections"

    def _online_users_key(self) -> str:
        return f"{self.key_prefix}:online_users"

    def _states_key(self) -> str:
        return f"{self.key_prefix}:states"

    async def add_connection(self, user_id: str, sid: str) -> bool:
        user_key = self._user_connections_key(user_id)
        online_key = self._online_users_key()
        states_key = self._states_key()

        async with self.redis.pipeline(transaction=True) as pipe:
            pipe.scard(user_key)
            pipe.sadd(user_key, sid)
            pipe.expire(user_key, 60 * 60 * 24)
            pipe.sadd(online_key, user_id)
            pipe.hset(states_key, user_id, "online")
            results = await pipe.execute()

        previous_count = int(results[0] or 0)

        if previous_count == 0:
            await self.redis.sadd(online_key, user_id)
            return True

        return False

    async def remove_connection(self, user_id: str, sid: str) -> bool:
        user_key = self._user_connections_key(user_id)
        online_key = self._online_users_key()

        async with self.redis.pipeline(transaction=True) as pipe:
            pipe.srem(user_key, sid)
            pipe.scard(user_key)
            results = await pipe.execute()

        remaining_count = int(results[1] or 0)

        if remaining_count <= 0:
            await self.redis.delete(user_key)
            await self.redis.srem(online_key, user_id)
            await self.redis.hset(self._states_key(), user_id, "offline")
            return True

        return False

    async def is_online(self, user_id: str) -> bool:
        online_key = self._online_users_key()
        return bool(await self.redis.sismember(online_key, user_id))

    async def get_state(self, user_id: str) -> PresenceState:
        if not await self.is_online(user_id):
            return "offline"
        value = await self.redis.hget(self._states_key(), user_id)
        state = value.decode() if isinstance(value, bytes) else str(value or "online")
        return cast(PresenceState, state) if state in {"online", "away", "dnd"} else "online"

    async def set_state(self, user_id: str, state: PresenceState) -> PresenceState:
        if state == "offline":
            await self.redis.hset(self._states_key(), user_id, "offline")
            await self.redis.srem(self._online_users_key(), user_id)
            return state
        if not await self.is_online(user_id):
            return "offline"
        await self.redis.hset(self._states_key(), user_id, state)
        await self.redis.sadd(self._online_users_key(), user_id)
        return state

    async def get_online_user_ids(self) -> list[str]:
        online_key = self._online_users_key()
        values = await self.redis.smembers(online_key)
        return sorted(v.decode() if isinstance(v, bytes) else str(v) for v in values)

    async def get_connection_count(self, user_id: str) -> int:
        user_key = self._user_connections_key(user_id)
        return int(await self.redis.scard(user_key))
