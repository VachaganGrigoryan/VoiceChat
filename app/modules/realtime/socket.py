from __future__ import annotations

from typing import Any

import socketio
from fastapi.encoders import jsonable_encoder


sio = socketio.AsyncServer(
    async_mode="asgi",
    cors_allowed_origins="*",
    logger=False,
    engineio_logger=False,
)


async def emit_to_user(user_id: str, event: str, payload: dict[str, Any]) -> None:
    await sio.emit(
        event,
        jsonable_encoder(payload),
        room=f"user:{user_id}",
    )


async def emit_voice_message_to_receiver(receiver_id: str, payload: dict[str, Any]) -> None:
    await emit_to_user(receiver_id, "receive_voice_message", payload)


async def emit_message_status_to_user(user_id: str, payload: dict[str, Any]) -> None:
    await emit_to_user(user_id, "voice_message_status", payload)


async def emit_presence_update(user_id: str, online: bool, skip_sid: str | None = None) -> None:
    await sio.emit(
        "presence_update",
        jsonable_encoder({"user_id": user_id, "online": online}),
        skip_sid=skip_sid,
    )


def register_socket_events() -> None:
    # Import here to avoid circular imports
    from app.modules.realtime.events import register_events

    register_events(sio)