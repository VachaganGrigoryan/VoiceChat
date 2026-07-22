from __future__ import annotations

import asyncio
import json
import logging
from typing import Protocol

import aio_pika
from aio_pika.abc import AbstractIncomingMessage

from app.core.config import settings
from app.core.logging import setup_logging
from app.modules.notifications.dependencies import get_notifications_service

log = logging.getLogger("app.push_worker")


class PushProvider(Protocol):
    async def deliver(self, *, tokens: list[str], payload: dict) -> list[str]: ...


class LoggingPushProvider:
    async def deliver(self, *, tokens: list[str], payload: dict) -> list[str]:
        log.info("push delivery intent tokens=%s", len(tokens))
        return []


provider: PushProvider = LoggingPushProvider()


async def handle_message(message: AbstractIncomingMessage) -> None:
    async with message.process(requeue=False):
        payload = json.loads(message.body.decode("utf-8"))
        job_type = payload.get("type")

        if job_type != "deliver_push_notification":
            log.warning("unknown push job type: %s", job_type)
            return

        tokens = [str(token) for token in payload.get("tokens", []) if token]
        invalid_tokens = await provider.deliver(tokens=tokens, payload=payload)
        if invalid_tokens:
            service = get_notifications_service()
            pruned = await service.prune_invalid_tokens(tokens=invalid_tokens)
            log.info("pruned invalid push tokens count=%s", pruned)


async def main() -> None:
    setup_logging()

    while True:
        try:
            log.info("connecting to rabbitmq at %s", settings.rabbitmq_url)
            connection = await aio_pika.connect_robust(settings.rabbitmq_url)

            async with connection:
                channel = await connection.channel()
                await channel.set_qos(prefetch_count=10)

                queue = await channel.declare_queue(
                    settings.push_queue_name,
                    durable=True,
                )

                await queue.consume(handle_message)

                log.info("push worker consuming queue=%s", settings.push_queue_name)
                await asyncio.Future()

        except Exception as e:
            log.exception("push worker connection failed: %s", e)
            await asyncio.sleep(5)


if __name__ == "__main__":
    asyncio.run(main())
