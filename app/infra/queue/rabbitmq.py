from __future__ import annotations

import json
from typing import Any, Optional

import aio_pika
from aio_pika import DeliveryMode, Message
from aio_pika.abc import AbstractRobustChannel, AbstractRobustConnection

from app.infra.queue.base import JobQueue


class RabbitMQQueue(JobQueue):
    def __init__(self, url: str):
        self.url = url
        self._connection: Optional[AbstractRobustConnection] = None
        self._channel: Optional[AbstractRobustChannel] = None

    async def connect(self) -> None:
        if self._connection and not self._connection.is_closed:
            return

        self._connection = await aio_pika.connect_robust(self.url)
        self._channel = await self._connection.channel()
        await self._channel.set_qos(prefetch_count=10)

    async def close(self) -> None:
        if self._channel and not self._channel.is_closed:
            await self._channel.close()

        if self._connection and not self._connection.is_closed:
            await self._connection.close()

        self._channel = None
        self._connection = None

    async def publish(self, *, queue_name: str, payload: dict[str, Any]) -> None:
        await self.connect()

        assert self._channel is not None

        queue = await self._channel.declare_queue(queue_name, durable=True)

        await self._channel.default_exchange.publish(
            Message(
                body=json.dumps(payload).encode("utf-8"),
                content_type="application/json",
                delivery_mode=DeliveryMode.PERSISTENT,
            ),
            routing_key=queue.name,
        )