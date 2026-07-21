from __future__ import annotations

from app.core.errors import AppError
from app.modules.calls.ws import (
    handle_call_socket_connect,
    handle_call_socket_disconnect,
)
from app.modules.conversations.dependencies import get_conversations_service
from app.modules.messages.dependencies import get_messages_service
from app.modules.calls.ws import register_events as register_call_events
from app.modules.realtime.auth import authenticate_socket, get_socket_user_id
from app.modules.realtime.emits import (
    emit_message_status_to_user,
    emit_presence_update,
)
from app.modules.realtime.presence import get_presence_backend


def register_events(sio) -> None:
    register_call_events(sio)

    async def get_conversation_participant_ids(
        *, user_id: str, conversation_id: str
    ) -> list[str]:
        service = get_conversations_service()
        conversation = await service.require_participant(
            user_id=user_id,
            conversation_id=conversation_id,
        )
        return [str(participant_id) for participant_id in conversation.participant_ids]

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

        conversation_id = (data or {}).get("conversation_id")
        if not conversation_id:
            await sio.emit(
                "error",
                {"code": "INVALID_PAYLOAD", "message": "conversation_id is required"},
                to=sid,
            )
            return

        try:
            participant_ids = await get_conversation_participant_ids(
                user_id=user_id,
                conversation_id=conversation_id,
            )
        except AppError as e:
            await sio.emit("error", {"code": e.code, "message": e.message}, to=sid)
            return

        for participant_id in participant_ids:
            if participant_id != user_id:
                await sio.emit(
                    "typing_start",
                    {"from": user_id, "conversation_id": conversation_id},
                    room=f"user:{participant_id}",
                )

    @sio.event
    async def typing_stop(sid, data):
        user_id = await get_socket_user_id(sio, sid)
        if not user_id:
            return

        conversation_id = (data or {}).get("conversation_id")
        if not conversation_id:
            await sio.emit(
                "error",
                {"code": "INVALID_PAYLOAD", "message": "conversation_id is required"},
                to=sid,
            )
            return

        try:
            participant_ids = await get_conversation_participant_ids(
                user_id=user_id,
                conversation_id=conversation_id,
            )
        except AppError as e:
            await sio.emit("error", {"code": e.code, "message": e.message}, to=sid)
            return

        for participant_id in participant_ids:
            if participant_id != user_id:
                await sio.emit(
                    "typing_stop",
                    {"from": user_id, "conversation_id": conversation_id},
                    room=f"user:{participant_id}",
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

        conversation_id = (data or {}).get("conversation_id")
        message_id = (data or {}).get("message_id")
        message_type = (data or {}).get("type")

        if not conversation_id or not message_id:
            await sio.emit(
                "error",
                {
                    "code": "INVALID_PAYLOAD",
                    "message": "conversation_id and message_id are required",
                },
                to=sid,
            )
            return

        try:
            await get_conversation_participant_ids(
                user_id=user_id,
                conversation_id=conversation_id,
            )
        except AppError as e:
            await sio.emit("error", {"code": e.code, "message": e.message}, to=sid)
            return

        await sio.emit(
            "send_message_ack",
            {
                "message_id": message_id,
                "conversation_id": conversation_id,
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

        payload = data or {}
        conversation_id = payload.get("conversation_id")
        message_id = payload.get("message_id")
        if not conversation_id or not message_id:
            await sio.emit(
                "error",
                {
                    "code": "INVALID_PAYLOAD",
                    "message": "conversation_id and message_id required",
                },
                to=sid,
            )
            return

        messages_service = get_messages_service()

        try:
            participant_ids = await get_conversation_participant_ids(
                user_id=receiver_id,
                conversation_id=conversation_id,
            )
            msg = await messages_service.mark_delivered_for_conversation(
                conversation_id=conversation_id,
                message_id=message_id,
                user_id=receiver_id,
            )
        except AppError as e:
            await sio.emit("error", {"code": e.code, "message": e.message}, to=sid)
            return

        for participant_id in participant_ids:
            if participant_id != receiver_id:
                await emit_message_status_to_user(
                    sio,
                    participant_id,
                    {
                        "conversation_id": conversation_id,
                        "message_id": message_id,
                        "receipt_summary": msg.receipt_summary.model_dump(mode="json"),
                        "updated_at": msg.updated_at,
                    },
                )

        await sio.emit(
            "message_ack",
            {
                "conversation_id": conversation_id,
                "message_id": message_id,
                "receipt_summary": msg.receipt_summary.model_dump(mode="json"),
            },
            to=sid,
        )

    @sio.event
    async def message_read(sid, data):
        receiver_id = await get_socket_user_id(sio, sid)
        if not receiver_id:
            return

        payload = data or {}
        conversation_id = payload.get("conversation_id")
        message_id = payload.get("message_id")
        if not conversation_id or not message_id:
            await sio.emit(
                "error",
                {
                    "code": "INVALID_PAYLOAD",
                    "message": "conversation_id and message_id required",
                },
                to=sid,
            )
            return

        conversations_service = get_conversations_service()
        messages_service = get_messages_service()

        try:
            participant_ids = await get_conversation_participant_ids(
                user_id=receiver_id,
                conversation_id=conversation_id,
            )
            msg = await messages_service.mark_read_for_conversation(
                conversation_id=conversation_id,
                message_id=message_id,
                user_id=receiver_id,
            )
            await conversations_service.mark_conversation_read(
                user_id=receiver_id,
                conversation_id=conversation_id,
                last_read_message_id=message_id,
            )
        except AppError as e:
            await sio.emit("error", {"code": e.code, "message": e.message}, to=sid)
            return

        for participant_id in participant_ids:
            if participant_id != receiver_id:
                await emit_message_status_to_user(
                    sio,
                    participant_id,
                    {
                        "conversation_id": conversation_id,
                        "message_id": message_id,
                        "receipt_summary": msg.receipt_summary.model_dump(mode="json"),
                        "updated_at": msg.updated_at,
                    },
                )

        await sio.emit(
            "message_ack",
            {
                "conversation_id": conversation_id,
                "message_id": message_id,
                "receipt_summary": msg.receipt_summary.model_dump(mode="json"),
            },
            to=sid,
        )

    @sio.event
    async def conversation_read(sid, data):
        receiver_id = await get_socket_user_id(sio, sid)
        if not receiver_id:
            return

        conversation_id = (data or {}).get("conversation_id")
        if not conversation_id:
            await sio.emit(
                "error",
                {"code": "INVALID_PAYLOAD", "message": "conversation_id required"},
                to=sid,
            )
            return

        service = get_conversations_service()

        try:
            participant_ids = await get_conversation_participant_ids(
                user_id=receiver_id,
                conversation_id=conversation_id,
            )
            await service.mark_conversation_read(
                user_id=receiver_id,
                conversation_id=conversation_id,
            )
        except AppError as e:
            await sio.emit("error", {"code": e.code, "message": e.message}, to=sid)
            return

        for participant_id in participant_ids:
            if participant_id != receiver_id:
                await sio.emit(
                    "conversation_read",
                    {"conversation_id": conversation_id, "user_id": receiver_id},
                    room=f"user:{participant_id}",
                )

        await sio.emit(
            "conversation_read_ack",
            {"conversation_id": conversation_id},
            to=sid,
        )
