from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

import socketio

from app.bots.poll.repository import PollRepository
from app.bots.poll.service import poll_broadcast_payload
from app.core.config import settings
from app.core.logging import setup_logging
from app.db.init import init_database
from app.db.mongo import connect_mongo
from app.modules.conversations.repository import ConversationsRepository
from app.modules.messages.dependencies import get_messages_service
from app.modules.messages.emit_helpers import emit_send_result
from app.modules.messages.service import MessagesService
from app.modules.notifications.dependencies import get_notifications_service
from app.modules.notifications.service import NotificationsService
from app.modules.realtime import emit_poll_updated, emit_to_user
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


async def close_due_polls(
    *,
    sio: socketio.AsyncServer,
    repo: PollRepository | None = None,
    now: datetime | None = None,
) -> int:
    """Close every poll past its deadline and broadcast the change to participants."""
    repo = repo or PollRepository()
    reference = now or datetime.now(UTC)
    closed = 0
    while True:
        poll = await repo.claim_due_for_close(now=reference)
        if poll is None:
            break
        conversation = await ConversationsRepository().get_by_id(
            str(poll.conversation_id)
        )
        participant_ids = (
            [str(pid) for pid in conversation.participant_ids]
            if conversation is not None
            else []
        )
        await emit_poll_updated(
            sio,
            participant_ids=participant_ids,
            payload=poll_broadcast_payload(poll),
        )
        closed += 1
    if closed:
        log.info("auto-closed due polls count=%s", closed)
    return closed


async def poll_close_forever(
    *,
    sio: socketio.AsyncServer,
    interval: int = POLL_INTERVAL_SECONDS,
) -> None:
    log.info("poll auto-close worker polling every %ss", interval)
    while True:
        try:
            await close_due_polls(sio=sio)
        except Exception as e:  # noqa: BLE001 - keep the poller alive across errors
            log.exception("poll auto-close sweep failed: %s", e)
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
