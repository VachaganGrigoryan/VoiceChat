from __future__ import annotations

from typing import TYPE_CHECKING

import socketio

from app.db.models import ConversationDocument
from app.modules.messages.schemas import MessageDoc
from app.modules.messages.service.base import SendMessageResult
from app.modules.notifications.service import NotificationsService
from app.modules.realtime import emit_to_channel, emit_to_user

if TYPE_CHECKING:
    from app.modules.relationships.service import RelationshipService


async def get_container_recipient_ids(
    *,
    container_type: str,
    container_id: str,
    conversation: ConversationDocument | None = None,
    relationships_service: RelationshipService | None = None,
) -> list[str]:
    """Resolve per-user recipient IDs for a conversation.

    Channels no longer resolve here: they broadcast once to their Socket.IO
    room (see `emit_to_container`), so there is no membership/follower lookup
    and no fan-out ceiling to cap.
    """
    if container_type == "conversation" and conversation is not None:
        return [str(pid) for pid in conversation.participant_ids]
    return []


async def emit_to_container(
    sio: socketio.AsyncServer,
    *,
    container_type: str,
    container_id: str,
    event: str,
    payload: dict,
    conversation: ConversationDocument | None = None,
    relationships_service: RelationshipService | None = None,
) -> None:
    """Fan an event out to everyone watching a container.

    Channels broadcast once to their room -- no membership/follower lookup,
    no fan-out ceiling. Conversations stay per-user: a DM or group's
    `participant_ids` is already small and bounded, so a room buys nothing.
    """
    if container_type == "channel":
        await emit_to_channel(sio, container_id, event, payload)
        return
    recipients = await get_container_recipient_ids(
        container_type=container_type,
        container_id=container_id,
        conversation=conversation,
        relationships_service=relationships_service,
    )
    for recipient_id in recipients:
        await emit_to_user(sio, recipient_id, event, payload)


async def emit_message_notifications(
    sio: socketio.AsyncServer,
    *,
    notifications: NotificationsService,
    message: MessageDoc,
) -> None:
    """Generate and emit in-app notifications for a newly created message."""
    generated = await notifications.generate_for_message(message=message)
    for item in generated:
        await emit_to_user(
            sio,
            item.notification.user_id,
            "notification_created",
            item.notification.model_dump(mode="json"),
        )


async def emit_send_result(
    sio: socketio.AsyncServer,
    *,
    result: SendMessageResult,
    participant_ids: list[str] | None = None,
) -> None:
    """Fan out a freshly-created message over realtime.

    Thread replies use `thread_reply_created` + `thread_summary_updated`; all other
    messages use `receive_message`. Event names stay snake_case and every payload
    carries the `{ container_type, container_id }` envelope.
    """
    payload = result.message.model_dump(mode="json")

    if result.thread_summary is not None:
        if participant_ids is not None:
            for participant_id in participant_ids:
                await emit_to_user(sio, participant_id, "thread_reply_created", payload)
                await emit_to_user(
                    sio,
                    participant_id,
                    "thread_summary_updated",
                    {
                        "thread_root_id": result.thread_summary.thread_root_id,
                        "container_type": result.thread_summary.container_type,
                        "container_id": result.thread_summary.container_id,
                        "conversation_id": result.thread_summary.conversation_id,
                        "thread_reply_count": result.thread_summary.thread_reply_count,
                        "last_thread_reply_at": result.thread_summary.last_thread_reply_at,
                    },
                )
            return
        return

    if participant_ids is not None:
        for participant_id in participant_ids:
            if participant_id != result.message.sender_id:
                await emit_to_user(sio, participant_id, "receive_message", payload)
        return


async def fan_out_container_send(
    sio: socketio.AsyncServer,
    *,
    container_type: str,
    container_id: str,
    result: SendMessageResult,
    notifications: NotificationsService,
    conversation: ConversationDocument | None = None,
) -> None:
    """Deliver a freshly created message to whoever watches its container.

    The one entry point the unified send routes use, so a message reaches the
    same audience whichever container it was addressed to: a channel broadcasts
    once to its room, a conversation goes per-participant. Notifications are
    generated the same way for both.
    """
    if container_type == "channel":
        await emit_send_result_to_channel(sio, container_id, result=result)
    else:
        await emit_send_result(
            sio,
            result=result,
            participant_ids=[
                str(participant_id)
                for participant_id in (
                    conversation.participant_ids if conversation is not None else []
                )
            ],
        )
    await emit_message_notifications(
        sio,
        notifications=notifications,
        message=result.message,
    )


async def emit_send_result_to_channel(
    sio: socketio.AsyncServer,
    channel_id: str,
    *,
    result: SendMessageResult,
) -> None:
    """Broadcast a freshly created channel message over its Socket.IO room.

    The sender's own connections are in the room too and receive it back --
    the client's cache writers already dedupe an incoming message by id, and
    this keeps a sender's other devices in sync without a second code path.
    """
    payload = result.message.model_dump(mode="json")

    if result.thread_summary is not None:
        await emit_to_channel(sio, channel_id, "thread_reply_created", payload)
        await emit_to_channel(
            sio,
            channel_id,
            "thread_summary_updated",
            {
                "thread_root_id": result.thread_summary.thread_root_id,
                "container_type": result.thread_summary.container_type,
                "container_id": result.thread_summary.container_id,
                "conversation_id": result.thread_summary.conversation_id,
                "thread_reply_count": result.thread_summary.thread_reply_count,
                "last_thread_reply_at": result.thread_summary.last_thread_reply_at,
            },
        )
        return

    await emit_to_channel(sio, channel_id, "receive_message", payload)
