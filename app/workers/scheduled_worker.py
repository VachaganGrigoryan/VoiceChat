from __future__ import annotations

import asyncio
import logging

import socketio

from app.core.config import settings
from app.core.logging import setup_logging
from app.db.init import init_database
from app.db.mongo import connect_mongo
from app.modules.messages.dependencies import get_messages_service
from app.modules.messages.emit_helpers import emit_send_result
from app.modules.messages.service import MessagesService
from app.modules.notifications.dependencies import get_notifications_service
from app.modules.notifications.service import NotificationsService
from app.modules.realtime import emit_to_user
from app.workers.socket_emitter import create_worker_emitter

log = logging.getLogger("app.scheduled_worker")

# How often to sweep for scheduled messages whose send time has arrived.
POLL_INTERVAL_SECONDS = getattr(settings, "scheduled_poll_interval_seconds", 15)


async def release_and_deliver(
    *,
    sio: socketio.AsyncServer,
    messages: MessagesService,
    notifications: NotificationsService,
) -> int:
    """Release every due scheduled message and fan it out like a live send."""
    released = await messages.release_due_scheduled_messages()
    for item in released:
        # Mirror the REST send path: realtime message + notification generation.
        await emit_send_result(
            sio,
            result=item.result,
            participant_ids=item.participant_ids,
        )
        generated = await notifications.generate_for_message(message=item.result.message)
        for notification in generated:
            await emit_to_user(
                sio,
                notification.notification.user_id,
                "notification_created",
                notification.notification.model_dump(mode="json"),
            )
    if released:
        log.info("released scheduled messages count=%s", len(released))
    return len(released)


async def poll_forever(
    *,
    sio: socketio.AsyncServer,
    messages: MessagesService,
    notifications: NotificationsService,
    interval: int = POLL_INTERVAL_SECONDS,
) -> None:
    log.info("scheduled worker polling every %ss", interval)
    while True:
        try:
            await release_and_deliver(
                sio=sio, messages=messages, notifications=notifications
            )
        except Exception as e:  # noqa: BLE001 - keep the poller alive across errors
            log.exception("scheduled release sweep failed: %s", e)
        await asyncio.sleep(interval)


async def main() -> None:
    setup_logging()
    await connect_mongo()
    await init_database()
    await poll_forever(
        sio=create_worker_emitter(),
        messages=get_messages_service(),
        notifications=get_notifications_service(),
    )


if __name__ == "__main__":
    asyncio.run(main())
