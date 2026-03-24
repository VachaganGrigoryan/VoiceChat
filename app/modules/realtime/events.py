from __future__ import annotations

from app.core.errors import AppError
from app.db.mongo import get_db
from app.modules.calls.ws import (
    handle_call_socket_connect,
    handle_call_socket_disconnect,
)
from app.modules.messages.dependencies import get_messages_service
from app.modules.messages.repository import MessagesRepository
from app.modules.calls.ws import register_events as register_call_events
from app.modules.realtime.auth import authenticate_socket, get_socket_user_id
from app.modules.realtime.emits import (
    emit_message_status_to_user,
    emit_presence_update,
)
from app.modules.realtime.presence import get_presence_backend


def register_events(sio) -> None:
    register_call_events(sio)

    async def ensure_chat_allowed(*, sender_id: str, receiver_id: str) -> None:
        service = get_messages_service()
        if service.pings_service is None:
            return
        await service.pings_service.ensure_can_message(
            sender_id=sender_id,
            receiver_id=receiver_id,
        )

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
            await emit_presence_update(sio, user_id, True, skip_sid=sid)

        await handle_call_socket_connect(sio, sid=sid, user_id=user_id)
        return True

    @sio.event
    async def disconnect(sid):
        user_id = await get_socket_user_id(sio, sid)
        if not user_id:
            return

        await handle_call_socket_disconnect(sio, sid=sid, user_id=user_id)

        presence = get_presence_backend()
        became_offline = await presence.remove_connection(user_id, sid)
        if became_offline:
            await emit_presence_update(sio, user_id, False)

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

        try:
            await ensure_chat_allowed(sender_id=user_id, receiver_id=peer_user_id)
        except AppError as e:
            await sio.emit("error", {"code": e.code, "message": e.message}, to=sid)
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

        try:
            await ensure_chat_allowed(sender_id=user_id, receiver_id=peer_user_id)
        except AppError as e:
            await sio.emit("error", {"code": e.code, "message": e.message}, to=sid)
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

        try:
            await ensure_chat_allowed(sender_id=user_id, receiver_id=receiver_id)
        except AppError as e:
            await sio.emit("error", {"code": e.code, "message": e.message}, to=sid)
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
            sio,
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
            sio,
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

    @sio.event
    async def conversation_read(sid, data):
        receiver_id = await get_socket_user_id(sio, sid)
        if not receiver_id:
            return

        peer_user_id = (data or {}).get("peer_user_id")
        if not peer_user_id:
            await sio.emit(
                "error",
                {"code": "INVALID_PAYLOAD", "message": "peer_user_id required"},
                to=sid,
            )
            return

        db = get_db()
        repo = MessagesRepository(db)

        updated_count = await repo.mark_conversation_read_for_receiver(
            receiver_id=receiver_id,
            peer_user_id=peer_user_id,
        )

        await sio.emit(
            "conversation_read_ack",
            {"peer_user_id": peer_user_id, "updated_count": updated_count},
            to=sid,
        )
