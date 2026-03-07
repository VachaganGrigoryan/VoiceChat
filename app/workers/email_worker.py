from __future__ import annotations

import asyncio
import json
import logging

import aio_pika
from aio_pika.abc import AbstractIncomingMessage

from app.core.config import settings
from app.core.logging import setup_logging
from app.infra.email.jobs import SendVerificationCodeJob
from app.infra.email.service import EmailService

log = logging.getLogger("app.email_worker")


async def handle_message(message: AbstractIncomingMessage) -> None:
    async with message.process(requeue=False):
        payload = json.loads(message.body.decode("utf-8"))
        job_type = payload.get("type")

        if job_type == "send_verification_code":
            job = SendVerificationCodeJob(**payload)
            service = EmailService()
            await service.send_verification_code(email=job.email, code=job.code)
            log.info("verification email sent to %s", job.email)
            return

        log.warning("unknown email job type: %s", job_type)


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
                    settings.email_queue_name,
                    durable=True,
                )

                await queue.consume(handle_message)

                log.info("email worker consuming queue=%s", settings.email_queue_name)
                await asyncio.Future()

        except Exception as e:
            log.exception("email worker connection failed: %s", e)
            await asyncio.sleep(5)


if __name__ == "__main__":
    asyncio.run(main())