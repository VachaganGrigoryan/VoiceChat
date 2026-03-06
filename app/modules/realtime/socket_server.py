from __future__ import annotations

import socketio
from typing import Any

from fastapi.encoders import jsonable_encoder

from app.core.security import decode_token
from app.core.exceptions import AppError
from app.db.mongo import get_db
from app.modules.messages.repository import MessagesRepository

# Async Socket.IO server (ASGI)
sio = socketio.AsyncServer(
    async_mode="asgi",
    cors_allowed_origins="*",  # tighten in production
    logger=False,
    engineio_logger=False,
)

# simple in-memory presence (works for single instance)
ONLINE_USERS: set[str] = set()


def _get_token_from_environ(environ: dict[str, Any]) -> str | None:
    # Socket.IO can pass token via:
    # - query string: ?token=...
    # - auth payload: io({ auth: { token } })
    qs = environ.get("QUERY_STRING", "")
    if "token=" in qs:
        # very small parser
        for part in qs.split("&"):
            if part.startswith("token="):
                return part.split("=", 1)[1]
    return None


@sio.event
async def connect(sid, environ, auth):
    """
    Authenticated connection.
    Client can send:
      io("http://localhost:8000", { auth: { token: "..." } })
    or:
      io("http://localhost:8000?token=...")
    """
    token = None
    if isinstance(auth, dict):
        token = auth.get("token")
    if not token:
        token = _get_token_from_environ(environ)

    if not token:
        return False  # rejects connection

    try:
        payload = decode_token(token)
    except AppError:
        return False

    user_id = payload.get("sub")
    if not user_id:
        return False

    # store user_id in session for this sid
    await sio.save_session(sid, {"user_id": user_id})

    # join room per user
    await sio.enter_room(sid, f"user:{user_id}")

    # presence (optional)
    ONLINE_USERS.add(user_id)
    await sio.emit("user_online", {"user_id": user_id}, skip_sid=sid)

    return True


@sio.event
async def disconnect(sid):
    session = await sio.get_session(sid)
    user_id = session.get("user_id") if session else None
    if user_id:
        ONLINE_USERS.discard(user_id)
        await sio.emit("user_offline", {"user_id": user_id})


@sio.event
async def ping(sid, data):
    return {"pong": True}


@sio.event
async def voice_message_delivered(sid, data):
    """
    Receiver -> server ACK.
    data: { "message_id": "..." }
    """
    session = await sio.get_session(sid)
    if not session or not session.get("user_id"):
        return  # unauthenticated socket

    receiver_id = session["user_id"]
    message_id = (data or {}).get("message_id")
    if not message_id:
        await sio.emit("error", {"code": "INVALID_PAYLOAD", "message": "message_id required"}, to=sid)
        return

    db = get_db()
    repo = MessagesRepository(db)

    try:
        msg = await repo.mark_delivered_for_receiver(message_id=message_id, receiver_id=receiver_id)
    except AppError as e:
        await sio.emit("error", {"code": e.code, "message": e.message}, to=sid)
        return

    sender_id = str(msg["sender_id"])

    # Notify sender
    await emit_message_status_to_user(
        sender_id,
        {
            "message_id": message_id,
            "status": "delivered",
            "delivered_at": msg.get("delivered_at"),
        },
    )

    # Optional: acknowledge receiver too (useful for client)
    await sio.emit(
        "voice_message_ack",
        {"message_id": message_id, "status": "delivered"},
        to=sid,
    )


@sio.event
async def voice_message_read(sid, data):
    """
    Receiver -> server read receipt.
    data: { "message_id": "..." }
    """
    session = await sio.get_session(sid)
    if not session or not session.get("user_id"):
        return

    receiver_id = session["user_id"]
    message_id = (data or {}).get("message_id")
    if not message_id:
        await sio.emit("error", {"code": "INVALID_PAYLOAD", "message": "message_id required"}, to=sid)
        return

    db = get_db()
    repo = MessagesRepository(db)

    try:
        msg = await repo.mark_read_for_receiver(message_id=message_id, receiver_id=receiver_id)
    except AppError as e:
        await sio.emit("error", {"code": e.code, "message": e.message}, to=sid)
        return

    sender_id = str(msg["sender_id"])

    await emit_message_status_to_user(
        sender_id,
        {
            "message_id": message_id,
            "status": "read",
            "read_at": msg.get("read_at"),
        },
    )

    await sio.emit(
        "voice_message_ack",
        {"message_id": message_id, "status": "read"},
        to=sid,
    )

async def emit_voice_message_to_receiver(receiver_id: str, payload: dict) -> None:
    # Ensure payload is JSON serializable (datetimes, ObjectId, etc.)
    safe_payload = jsonable_encoder(payload)
    await sio.emit("receive_voice_message", safe_payload, room=f"user:{receiver_id}")


async def emit_message_status_to_user(user_id: str, payload: dict) -> None:
    safe_payload = jsonable_encoder(payload)
    await sio.emit("voice_message_status", safe_payload, room=f"user:{user_id}")