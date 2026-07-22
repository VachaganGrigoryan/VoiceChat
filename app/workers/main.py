"""Combined worker entrypoint: email queue consumer + scheduled-send poller.

Both run concurrently in one process. The email consumer needs RabbitMQ; the
scheduled poller needs Mongo (for due lookups) and the Redis Socket.IO manager
(to deliver released messages to connected clients).
"""

from __future__ import annotations

import asyncio
import logging

from app.core.logging import setup_logging
from app.db.init import init_database
from app.db.mongo import connect_mongo
from app.modules.messages.dependencies import get_messages_service
from app.modules.notifications.dependencies import get_notifications_service
from app.workers.email_worker import consume_forever
from app.workers.scheduled_worker import poll_forever
from app.workers.socket_emitter import create_worker_emitter

log = logging.getLogger("app.worker")


async def main() -> None:
    setup_logging()
    await connect_mongo()
    await init_database()

    log.info("worker starting: email consumer + scheduled poller")
    await asyncio.gather(
        consume_forever(),
        poll_forever(
            sio=create_worker_emitter(),
            messages=get_messages_service(),
            notifications=get_notifications_service(),
        ),
    )


if __name__ == "__main__":
    asyncio.run(main())
