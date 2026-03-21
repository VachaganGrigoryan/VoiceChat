from __future__ import annotations

from typing import Any

import socketio
from fastapi.encoders import jsonable_encoder


def user_room(user_id: str) -> str:
    return f"user:{user_id}"


async def emit_to_user(sio: socketio.AsyncServer, user_id: str, event: str, payload: dict[str, Any]) -> None:
    await sio.emit(event, jsonable_encoder(payload), room=user_room(user_id))


async def emit_message_to_receiver(sio: socketio.AsyncServer, receiver_id: str, payload: dict[str, Any]) -> None:
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


async def emit_presence_update(sio: socketio.AsyncServer, user_id: str, online: bool, skip_sid: str | None = None) -> None:
    await sio.emit(
        "presence_update",
        jsonable_encoder({"user_id": user_id, "online": online}),
        skip_sid=skip_sid,
    )


async def emit_ping_received(sio: socketio.AsyncServer, *, to_user_id: str, payload: dict) -> None:
    await sio.emit("ping_received", payload, room=user_room(to_user_id))


async def emit_ping_accepted(sio: socketio.AsyncServer, *, to_user_id: str, payload: dict) -> None:
    await sio.emit("ping_accepted", payload, room=user_room(to_user_id))


async def emit_ping_declined(sio: socketio.AsyncServer, *, to_user_id: str, payload: dict) -> None:
    await sio.emit("ping_declined", payload, room=user_room(to_user_id))


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
