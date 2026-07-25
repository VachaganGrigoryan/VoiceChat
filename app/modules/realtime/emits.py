from __future__ import annotations

from datetime import datetime
from typing import Any

import socketio
from fastapi.encoders import jsonable_encoder

from app.modules.realtime.presence.base import PresenceState


def user_room(user_id: str) -> str:
    return f"user:{user_id}"


async def emit_to_user(sio: socketio.AsyncServer, user_id: str, event: str, payload: dict[str, Any]) -> None:
    await sio.emit(event, jsonable_encoder(payload), room=user_room(user_id))


async def emit_message_to_receiver(sio: socketio.AsyncServer, receiver_id: str, payload: dict[str, Any]) -> None:
    await emit_to_user(sio, receiver_id, "receive_message", payload)


async def emit_message_to_participants(
    sio: socketio.AsyncServer,
    *,
    sender_id: str,
    receiver_id: str,
    payload: dict[str, Any],
) -> None:
    await emit_to_user(sio, sender_id, "receive_message", payload)
    await emit_to_user(sio, receiver_id, "receive_message", payload)


async def emit_message_status_to_user(sio: socketio.AsyncServer, user_id: str, payload: dict[str, Any]) -> None:
    await emit_to_user(sio, user_id, "message_status", payload)


async def emit_message_edited(
        sio: socketio.AsyncServer,
        *,
        sender_id: str,
        receiver_id: str,
        payload: dict[str, Any],
) -> None:
    await emit_to_user(sio, sender_id, "message_edited", payload)
    await emit_to_user(sio, receiver_id, "message_edited", payload)


async def emit_message_deleted(
        sio: socketio.AsyncServer,
        *,
        sender_id: str,
        receiver_id: str,
        payload: dict[str, Any],
) -> None:
    await emit_to_user(sio, sender_id, "message_deleted", payload)
    await emit_to_user(sio, receiver_id, "message_deleted", payload)


async def emit_message_reacted(
        sio: socketio.AsyncServer,
        *,
        sender_id: str,
        receiver_id: str,
        payload: dict[str, Any],
) -> None:
    await emit_to_user(sio, sender_id, "message_reacted", payload)
    await emit_to_user(sio, receiver_id, "message_reacted", payload)


async def emit_poll_updated(
    sio: socketio.AsyncServer,
    *,
    participant_ids: list[str],
    payload: dict[str, Any],
) -> None:
    """Fan out a poll tally/close change to every conversation participant."""
    for participant_id in participant_ids:
        await emit_to_user(sio, participant_id, "poll_updated", payload)


async def emit_thread_reply_created(
        sio: socketio.AsyncServer,
        *,
        sender_id: str,
        receiver_id: str,
        payload: dict[str, Any],
) -> None:
    await emit_to_user(sio, sender_id, "thread_reply_created", payload)
    await emit_to_user(sio, receiver_id, "thread_reply_created", payload)


async def emit_thread_summary_updated(
        sio: socketio.AsyncServer,
        *,
        sender_id: str,
        receiver_id: str,
        payload: dict[str, Any],
) -> None:
    await emit_to_user(sio, sender_id, "thread_summary_updated", payload)
    await emit_to_user(sio, receiver_id, "thread_summary_updated", payload)


async def emit_presence_update(
    sio: socketio.AsyncServer,
    user_id: str,
    state: PresenceState,
    *,
    last_seen_at: datetime | None = None,
    skip_sid: str | None = None,
) -> None:
    await sio.emit(
        "presence_update",
        jsonable_encoder(
            {
                "user_id": user_id,
                "state": state,
                "status": state,
                "online": state != "offline",
                "last_seen_at": last_seen_at,
            }
        ),
        skip_sid=skip_sid,
    )


async def emit_ping_received(sio: socketio.AsyncServer, *, to_user_id: str, payload: dict) -> None:
    await sio.emit("ping_received", payload, room=user_room(to_user_id))


async def emit_ping_accepted(sio: socketio.AsyncServer, *, to_user_id: str, payload: dict) -> None:
    await sio.emit("ping_accepted", payload, room=user_room(to_user_id))


async def emit_ping_declined(sio: socketio.AsyncServer, *, to_user_id: str, payload: dict) -> None:
    await sio.emit("ping_declined", payload, room=user_room(to_user_id))


async def emit_ping_cancelled(sio: socketio.AsyncServer, *, to_user_id: str, payload: dict) -> None:
    await sio.emit("ping_cancelled", payload, room=user_room(to_user_id))


async def emit_user_blocked(sio: socketio.AsyncServer, *, user_a: str, user_b: str) -> None:
    await sio.emit("user_blocked", {"peer_user_id": user_b}, room=user_room(user_a))
    await sio.emit("user_blocked", {"peer_user_id": user_a}, room=user_room(user_b))


async def emit_chat_permission_updated(
    sio: socketio.AsyncServer,
    *,
    user_a: str,
    user_b: str,
    allowed: bool = True,
) -> None:
    payload = {
        "peer_user_id": user_b,
        "allowed": allowed,
    }
    await sio.emit("chat_permission_updated", payload, room=user_room(user_a))

    payload_reverse = {
        "peer_user_id": user_a,
        "allowed": allowed,
    }
    await sio.emit("chat_permission_updated", payload_reverse, room=user_room(user_b))


async def emit_space_invite(sio: socketio.AsyncServer, *, to_user_id: str, payload: dict) -> None:
    await sio.emit("space:invite", payload, room=user_room(to_user_id))


async def emit_relationship_event(
    sio: socketio.AsyncServer,
    *,
    event: str,
    to_user_ids: list[str],
    payload: dict[str, Any],
) -> None:
    """Fan a `relationship.*` lifecycle event out to both sides of the edge."""
    encoded = jsonable_encoder(payload)
    for user_id in dict.fromkeys(str(uid) for uid in to_user_ids if uid):
        await sio.emit(event, encoded, room=user_room(user_id))


async def emit_relationship_requested(
    sio: socketio.AsyncServer, *, to_user_ids: list[str], payload: dict[str, Any]
) -> None:
    await emit_relationship_event(
        sio, event="relationship.requested", to_user_ids=to_user_ids, payload=payload
    )


async def emit_relationship_activated(
    sio: socketio.AsyncServer, *, to_user_ids: list[str], payload: dict[str, Any]
) -> None:
    await emit_relationship_event(
        sio, event="relationship.activated", to_user_ids=to_user_ids, payload=payload
    )


async def emit_relationship_revoked(
    sio: socketio.AsyncServer, *, to_user_ids: list[str], payload: dict[str, Any]
) -> None:
    await emit_relationship_event(
        sio, event="relationship.revoked", to_user_ids=to_user_ids, payload=payload
    )
