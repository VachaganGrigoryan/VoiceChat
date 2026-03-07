from __future__ import annotations

from typing import Any

import aio_pika
from redis.asyncio import Redis

from app.core.config import settings
from app.db.mongo import get_db


class HealthService:
    async def check_mongo(self) -> dict[str, Any]:
        try:
            db = get_db()
            await db.command("ping")
            return {"status": "up"}
        except Exception as e:
            return {"status": "down", "error": str(e)}

    async def check_redis(self) -> dict[str, Any]:
        try:
            redis = Redis.from_url(settings.redis_url, encoding="utf-8", decode_responses=True)
            await redis.ping()
            await redis.aclose()
            return {"status": "up"}
        except Exception as e:
            return {"status": "down", "error": str(e)}

    async def check_rabbitmq(self) -> dict[str, Any]:
        try:
            connection = await aio_pika.connect_robust(settings.rabbitmq_url)
            async with connection:
                channel = await connection.channel()
                await channel.close()
            return {"status": "up"}
        except Exception as e:
            return {"status": "down", "error": str(e)}

    async def check_storage(self) -> dict[str, Any]:
        """
        Optional lightweight storage check.
        For now we report configured backend as healthy unless you want
        an actual bucket/object probe.
        """
        try:
            return {
                "status": "up",
                "provider": settings.storage_provider,
            }
        except Exception as e:
            return {"status": "down", "error": str(e)}

    async def readiness(self) -> dict[str, Any]:
        mongo = await self.check_mongo()
        rabbitmq = await self.check_rabbitmq()
        storage = await self.check_storage()

        checks = {
            "mongo": mongo,
            "rabbitmq": rabbitmq,
            "storage": storage,
        }

        if settings.presence_backend == "redis" or settings.socketio_queue_backend == "redis":
            checks["redis"] = await self.check_redis()

        overall = "up" if all(v["status"] == "up" for v in checks.values()) else "down"

        return {
            "status": overall,
            "checks": checks,
        }