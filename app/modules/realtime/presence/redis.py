from __future__ import annotations

from redis.asyncio import Redis

from app.modules.realtime.presence.base import PresenceBackend


class RedisPresenceBackend(PresenceBackend):
    def __init__(self, redis: Redis, key_prefix: str = "presence") -> None:
        self.redis = redis
        self.key_prefix = key_prefix

    def _user_connections_key(self, user_id: str) -> str:
        return f"{self.key_prefix}:user:{user_id}:connections"

    def _online_users_key(self) -> str:
        return f"{self.key_prefix}:online_users"

    async def add_connection(self, user_id: str, sid: str) -> bool:
        user_key = self._user_connections_key(user_id)
        online_key = self._online_users_key()

        async with self.redis.pipeline(transaction=True) as pipe:
            pipe.scard(user_key)
            pipe.sadd(user_key, sid)
            pipe.expire(user_key, 60 * 60 * 24)
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
            return True

        return False

    async def is_online(self, user_id: str) -> bool:
        online_key = self._online_users_key()
        return bool(await self.redis.sismember(online_key, user_id))

    async def get_online_user_ids(self) -> list[str]:
        online_key = self._online_users_key()
        values = await self.redis.smembers(online_key)
        return sorted(v.decode() if isinstance(v, bytes) else str(v) for v in values)

    async def get_connection_count(self, user_id: str) -> int:
        user_key = self._user_connections_key(user_id)
        return int(await self.redis.scard(user_key))