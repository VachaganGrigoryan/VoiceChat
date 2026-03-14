from __future__ import annotations

from app.core.errors import AppError
from app.db.mongo import get_db
from app.modules.messages.repository import MessagesRepository
from app.modules.realtime.auth import authenticate_socket, get_socket_user_id
from app.modules.realtime.presence import get_presence_backend
from app.modules.realtime.emits import (
    emit_message_status_to_user,
    emit_presence_update,
)


def register_events(sio) -> None:
    @sio.event
    async def connect(sid, environ, auth):
        try:
            user_id = authenticate_socket(environ, auth)
        except AppError:
            return False

        await sio.save_session(sid, {"user_id": user_id})
        await sio.enter_room(sid, f"user:{user_id}")

        presence = get_presence_backend()
        became_online = await presence.add_connection(user_id, sid)
        if became_online:
            await emit_presence_update(user_id, True, skip_sid=sid)

        return True

    @sio.event
    async def disconnect(sid):
        user_id = await get_socket_user_id(sio, sid)
        if not user_id:
            return

        presence = get_presence_backend()
        became_offline = await presence.remove_connection(user_id, sid)
        if became_offline:
            await emit_presence_update(user_id, False)

    @sio.event
    async def ping(sid, data):
        return {"pong": True}

    @sio.event
    async def typing_start(sid, data):
        user_id = await get_socket_user_id(sio, sid)
        if not user_id:
            return

        peer_user_id = (data or {}).get("to")
        if not peer_user_id:
            await sio.emit(
                "error",
                {"code": "INVALID_PAYLOAD", "message": "`to` is required"},
                to=sid,
            )
            return

        await sio.emit(
            "typing_start",
            {"from": user_id},
            room=f"user:{peer_user_id}",
        )

    @sio.event
    async def typing_stop(sid, data):
        user_id = await get_socket_user_id(sio, sid)
        if not user_id:
            return

        peer_user_id = (data or {}).get("to")
        if not peer_user_id:
            await sio.emit(
                "error",
                {"code": "INVALID_PAYLOAD", "message": "`to` is required"},
                to=sid,
            )
            return

        await sio.emit(
            "typing_stop",
            {"from": user_id},
            room=f"user:{peer_user_id}",
        )

    @sio.event
    async def send_message(sid, data):
        """
        Socket-level compatibility / ack event.
        REST remains the source of truth for persisted message creation.
        """
        user_id = await get_socket_user_id(sio, sid)
        if not user_id:
            return

        receiver_id = (data or {}).get("to")
        message_id = (data or {}).get("message_id")
        message_type = (data or {}).get("type")

        if not receiver_id or not message_id:
            await sio.emit(
                "error",
                {"code": "INVALID_PAYLOAD", "message": "`to` and `message_id` are required"},
                to=sid,
            )
            return

        await sio.emit(
            "send_message_ack",
            {
                "message_id": message_id,
                "to": receiver_id,
                "message_type": message_type,
                "accepted": True,
            },
            to=sid,
        )

    @sio.event
    async def message_delivered(sid, data):
        receiver_id = await get_socket_user_id(sio, sid)
        if not receiver_id:
            return

        message_id = (data or {}).get("message_id")
        if not message_id:
            await sio.emit(
                "error",
                {"code": "INVALID_PAYLOAD", "message": "message_id required"},
                to=sid,
            )
            return

        db = get_db()
        repo = MessagesRepository(db)

        try:
            msg = await repo.mark_delivered_for_receiver(
                message_id=message_id,
                receiver_id=receiver_id,
            )
        except AppError as e:
            await sio.emit("error", {"code": e.code, "message": e.message}, to=sid)
            return

        sender_id = str(msg["sender_id"])

        await emit_message_status_to_user(
            sender_id,
            {
                "message_id": message_id,
                "status": "delivered",
                "message_type": msg.get("type"),
                "delivered_at": msg.get("delivered_at"),
            },
        )

        await sio.emit(
            "message_ack",
            {"message_id": message_id, "status": "delivered"},
            to=sid,
        )

    @sio.event
    async def message_read(sid, data):
        receiver_id = await get_socket_user_id(sio, sid)
        if not receiver_id:
            return

        message_id = (data or {}).get("message_id")
        if not message_id:
            await sio.emit(
                "error",
                {"code": "INVALID_PAYLOAD", "message": "message_id required"},
                to=sid,
            )
            return

        db = get_db()
        repo = MessagesRepository(db)

        try:
            msg = await repo.mark_read_for_receiver(
                message_id=message_id,
                receiver_id=receiver_id,
            )
        except AppError as e:
            await sio.emit("error", {"code": e.code, "message": e.message}, to=sid)
            return

        sender_id = str(msg["sender_id"])

        await emit_message_status_to_user(
            sender_id,
            {
                "message_id": message_id,
                "status": "read",
                "message_type": msg.get("type"),
                "read_at": msg.get("read_at"),
            },
        )

        await sio.emit(
            "message_ack",
            {"message_id": message_id, "status": "read"},
            to=sid,
        )

    # Todo: Optional backward-compatible aliases. Remove later
    @sio.event
    async def send_voice_message(sid, data):
        return await send_message(sid, data)

    @sio.event
    async def voice_message_delivered(sid, data):
        return await message_delivered(sid, data)

    @sio.event
    async def voice_message_read(sid, data):
        return await message_read(sid, data)