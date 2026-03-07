from __future__ import annotations

from app.core.config import settings
from app.infra.queue.rabbitmq import RabbitMQQueue

_job_queue: RabbitMQQueue | None = None


def get_job_queue() -> RabbitMQQueue:
    global _job_queue

    if _job_queue is None:
        _job_queue = RabbitMQQueue(settings.rabbitmq_url)

    return _job_queue