from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import UTC, datetime
from typing import Any

import socketio
from fastapi.encoders import jsonable_encoder

from app.core.errors import AppError
from app.modules.calls.dependencies import get_calls_service
from app.modules.calls.schemas import (
    CallActionPayload,
    CallAnswerPayload,
    CallDoc,
    CallIceCandidatePayload,
    CallMediaStatePayload,
    CallOfferPayload,
    CallParticipantUpdatedEvent,
)
from app.modules.calls.session_registry import (
    close_call_session_registry,
    get_call_session_registry,
)
from app.modules.realtime.auth import get_socket_user_id
from app.modules.realtime.emits import (
    emit_message_to_participants,
    emit_to_user,
    user_room,
)

_expiration_tasks: dict[str, asyncio.Task[None]] = {}
_reconnect_timeout_tasks: dict[str, asyncio.Task[None]] = {}


def register_events(sio: socketio.AsyncServer) -> None:
    @sio.on("call.join")
    async def handle_call_join(sid: str, data: dict[str, Any] | None):
        user_id = await get_socket_user_id(sio, sid)
        if not user_id:
            return

        try:
            payload = CallActionPayload.model_validate(data or {})
            service = get_calls_service()
            current_call = await service.get_participant_call(
                user_id=user_id, call_id=payload.call_id
            )
            join_state = (
                service.to_call_doc(current_call).participant_states[user_id].join_state
            )
            await _bind_socket_to_call_room(
                sio,
                socket_id=sid,
                room_id=current_call["room_id"],
                call_id=payload.call_id,
                user_id=user_id,
            )
            call_doc = await service.mark_participant_joined(
                user_id=user_id, call_id=payload.call_id
            )
            if join_state != "joined":
                await emit_call_participant_updated_event(
                    sio,
                    service=service,
                    call_doc=call_doc,
                    actor_user_id=user_id,
                    reason="joined",
                    skip_actor_socket_id=sid,
                )
        except AppError as exc:
            await _emit_socket_error(sio, sid, exc)
            return
        except Exception:
            await _emit_invalid_payload(sio, sid, message="`call_id` is required")
            return

    @sio.on("call.offer")
    async def handle_call_offer(sid: str, data: dict[str, Any] | None):
        user_id = await get_socket_user_id(sio, sid)
        if not user_id:
            return

        try:
            payload = CallOfferPayload.model_validate(data or {})
            service = get_calls_service()
            call_doc = await service.start_connecting(
                user_id=user_id, call_id=payload.call_id
            )
            join_state = (
                service.to_call_doc(call_doc).participant_states[user_id].join_state
            )
            await _bind_socket_to_call_room(
                sio,
                socket_id=sid,
                room_id=call_doc["room_id"],
                call_id=payload.call_id,
                user_id=user_id,
            )
            if join_state != "joined":
                call_doc = await service.mark_participant_joined(
                    user_id=user_id, call_id=payload.call_id
                )
                await emit_call_participant_updated_event(
                    sio,
                    service=service,
                    call_doc=call_doc,
                    actor_user_id=user_id,
                    reason="joined",
                    skip_actor_socket_id=sid,
                )
            cancel_call_reconnect_timeout(payload.call_id)
        except AppError as exc:
            await _emit_socket_error(sio, sid, exc)
            return
        except Exception:
            await _emit_invalid_payload(
                sio, sid, message="`call_id` and `sdp` are required"
            )
            return

        await sio.emit(
            "call.offer",
            jsonable_encoder(
                {
                    "call_id": payload.call_id,
                    "from_user_id": user_id,
                    "sdp": payload.sdp,
                }
            ),
            room=call_doc["room_id"],
            skip_sid=sid,
        )

    @sio.on("call.answer")
    async def handle_call_answer(sid: str, data: dict[str, Any] | None):
        user_id = await get_socket_user_id(sio, sid)
        if not user_id:
            return

        try:
            payload = CallAnswerPayload.model_validate(data or {})
            service = get_calls_service()
            call_doc = await service.ensure_answer_allowed(
                user_id=user_id, call_id=payload.call_id
            )
            join_state = (
                service.to_call_doc(call_doc).participant_states[user_id].join_state
            )
            await _bind_socket_to_call_room(
                sio,
                socket_id=sid,
                room_id=call_doc["room_id"],
                call_id=payload.call_id,
                user_id=user_id,
            )
            if join_state != "joined":
                call_doc = await service.mark_participant_joined(
                    user_id=user_id, call_id=payload.call_id
                )
                await emit_call_participant_updated_event(
                    sio,
                    service=service,
                    call_doc=call_doc,
                    actor_user_id=user_id,
                    reason="joined",
                    skip_actor_socket_id=sid,
                )
        except AppError as exc:
            await _emit_socket_error(sio, sid, exc)
            return
        except Exception:
            await _emit_invalid_payload(
                sio, sid, message="`call_id` and `sdp` are required"
            )
            return

        await sio.emit(
            "call.answer",
            jsonable_encoder(
                {
                    "call_id": payload.call_id,
                    "from_user_id": user_id,
                    "sdp": payload.sdp,
                }
            ),
            room=call_doc["room_id"],
            skip_sid=sid,
        )

    @sio.on("call.ice_candidate")
    async def handle_call_ice_candidate(sid: str, data: dict[str, Any] | None):
        user_id = await get_socket_user_id(sio, sid)
        if not user_id:
            return

        try:
            payload = CallIceCandidatePayload.model_validate(data or {})
            service = get_calls_service()
            call_doc = await service.ensure_ice_candidate_allowed(
                user_id=user_id,
                call_id=payload.call_id,
            )
            join_state = (
                service.to_call_doc(call_doc).participant_states[user_id].join_state
            )
            await _bind_socket_to_call_room(
                sio,
                socket_id=sid,
                room_id=call_doc["room_id"],
                call_id=payload.call_id,
                user_id=user_id,
            )
            if join_state != "joined":
                call_doc = await service.mark_participant_joined(
                    user_id=user_id, call_id=payload.call_id
                )
                await emit_call_participant_updated_event(
                    sio,
                    service=service,
                    call_doc=call_doc,
                    actor_user_id=user_id,
                    reason="joined",
                    skip_actor_socket_id=sid,
                )
        except AppError as exc:
            await _emit_socket_error(sio, sid, exc)
            return
        except Exception:
            await _emit_invalid_payload(
                sio, sid, message="`call_id` and `candidate` are required"
            )
            return

        await sio.emit(
            "call.ice_candidate",
            jsonable_encoder(
                {
                    "call_id": payload.call_id,
                    "from_user_id": user_id,
                    "candidate": payload.candidate,
                }
            ),
            room=call_doc["room_id"],
            skip_sid=sid,
        )

    @sio.on("call.media_state")
    async def handle_call_media_state(sid: str, data: dict[str, Any] | None):
        user_id = await get_socket_user_id(sio, sid)
        if not user_id:
            return

        try:
            payload = CallMediaStatePayload.model_validate(data or {})
            service = get_calls_service()
            current_call = await service.get_participant_call(
                user_id=user_id, call_id=payload.call_id
            )
            current_state = service.to_call_doc(current_call).participant_states[
                user_id
            ]
            has_change = (
                payload.audio_enabled is not None
                and payload.audio_enabled != current_state.audio_enabled
            ) or (
                payload.video_enabled is not None
                and payload.video_enabled != current_state.video_enabled
            )
            call_doc = await service.update_media_state(
                user_id=user_id,
                call_id=payload.call_id,
                audio_enabled=payload.audio_enabled,
                video_enabled=payload.video_enabled,
            )
            await _bind_socket_to_call_room(
                sio,
                socket_id=sid,
                room_id=call_doc["room_id"],
                call_id=payload.call_id,
                user_id=user_id,
            )
            if has_change:
                await emit_call_participant_updated_event(
                    sio,
                    service=service,
                    call_doc=call_doc,
                    actor_user_id=user_id,
                    reason="media_updated",
                    skip_actor_socket_id=sid,
                )
        except AppError as exc:
            await _emit_socket_error(sio, sid, exc)
            return
        except Exception:
            await _emit_invalid_payload(
                sio,
                sid,
                message="`call_id` and at least one media state field are required",
            )
            return

    @sio.on("call.connected")
    async def handle_call_connected(sid: str, data: dict[str, Any] | None):
        user_id = await get_socket_user_id(sio, sid)
        if not user_id:
            return

        try:
            payload = CallActionPayload.model_validate(data or {})
            service = get_calls_service()
            call_doc = await service.mark_active(
                user_id=user_id, call_id=payload.call_id
            )
            await _bind_socket_to_call_room(
                sio,
                socket_id=sid,
                room_id=call_doc["room_id"],
                call_id=payload.call_id,
                user_id=user_id,
            )
            cancel_call_reconnect_timeout(payload.call_id)
            await emit_call_state_event(
                sio,
                event="call.connected",
                service=service,
                call_doc=call_doc,
            )
        except AppError as exc:
            await _emit_socket_error(sio, sid, exc)
            return
        except Exception:
            await _emit_invalid_payload(sio, sid, message="`call_id` is required")

    @sio.on("call.hangup")
    async def handle_call_hangup(sid: str, data: dict[str, Any] | None):
        user_id = await get_socket_user_id(sio, sid)
        if not user_id:
            return

        try:
            payload = CallActionPayload.model_validate(data or {})
            service = get_calls_service()
            result = await service.end_call(user_id=user_id, call_id=payload.call_id)
            cancel_call_expiration(payload.call_id)
            cancel_call_reconnect_timeout(payload.call_id)
            await clear_call_bindings(
                call_id=payload.call_id,
                participant_user_ids=list(result.call.participant_user_ids),
            )
            await emit_call_state_event(
                sio,
                event="call.ended",
                service=service,
                call_doc=result.call,
            )
            await _emit_history_message_if_any(
                sio,
                history_message=result.history_message,
            )
        except AppError as exc:
            await _emit_socket_error(sio, sid, exc)
            return
        except Exception:
            await _emit_invalid_payload(sio, sid, message="`call_id` is required")

    @sio.on("call.reject")
    async def handle_call_reject(sid: str, data: dict[str, Any] | None):
        user_id = await get_socket_user_id(sio, sid)
        if not user_id:
            return

        try:
            payload = CallActionPayload.model_validate(data or {})
            service = get_calls_service()
            result = await service.reject_call(user_id=user_id, call_id=payload.call_id)
            cancel_call_expiration(payload.call_id)
            cancel_call_reconnect_timeout(payload.call_id)
            await clear_call_bindings(
                call_id=payload.call_id,
                participant_user_ids=list(result.call.participant_user_ids),
            )
            await emit_call_state_event(
                sio,
                event="call.rejected",
                service=service,
                call_doc=result.call,
            )
            await _emit_history_message_if_any(
                sio,
                history_message=result.history_message,
            )
        except AppError as exc:
            await _emit_socket_error(sio, sid, exc)
            return
        except Exception:
            await _emit_invalid_payload(sio, sid, message="`call_id` is required")

    @sio.on("call.resume")
    async def handle_call_resume(sid: str, data: dict[str, Any] | None):
        user_id = await get_socket_user_id(sio, sid)
        if not user_id:
            return

        try:
            payload = CallActionPayload.model_validate(data or {})
            service = get_calls_service()
            current_call = await service.get_participant_call(
                user_id=user_id, call_id=payload.call_id
            )
            model = service.to_call_doc(current_call)
            registry = get_call_session_registry()
            connection_count = await registry.get_connection_count(
                call_id=model.id,
                user_id=user_id,
            )

            if connection_count > 0 and user_id not in model.disconnected_user_ids:
                raise AppError(
                    code="CALL_NOT_RECOVERABLE",
                    message="Call is already bound to another active socket",
                    status_code=409,
                )

            call_doc = await service.resume_call(
                user_id=user_id, call_id=payload.call_id
            )
            await _bind_socket_to_call_room(
                sio,
                socket_id=sid,
                room_id=call_doc["room_id"],
                call_id=payload.call_id,
                user_id=user_id,
            )
            if call_doc.get("status") == "reconnecting":
                schedule_call_reconnect_timeout(
                    sio,
                    call_id=payload.call_id,
                    reconnect_deadline_at=call_doc.get("reconnect_deadline_at"),
                )
            await emit_call_state_event(
                sio,
                event="call.resumed",
                service=service,
                call_doc=call_doc,
                include_ice_servers=True,
            )
            await emit_call_participant_updated_event(
                sio,
                service=service,
                call_doc=call_doc,
                actor_user_id=user_id,
                reason="resumed",
                skip_actor_socket_id=sid,
            )
        except AppError as exc:
            await _emit_socket_error(sio, sid, exc)
            return
        except Exception:
            await _emit_invalid_payload(sio, sid, message="`call_id` is required")


async def emit_call_incoming_event(
    sio: socketio.AsyncServer,
    *,
    service,
    call_doc: dict[str, Any] | CallDoc,
) -> None:
    model = service.to_call_doc(call_doc)
    payload = await service.build_session(
        call_doc=model,
        viewer_user_id=model.callee_user_id,
        include_ice_servers=True,
    )
    await emit_to_user(
        sio,
        model.callee_user_id,
        "call.incoming",
        payload.model_dump(mode="json"),
    )


async def emit_call_state_event(
    sio: socketio.AsyncServer,
    *,
    event: str,
    service,
    call_doc: dict[str, Any] | CallDoc,
    include_ice_servers: bool = False,
) -> None:
    model = service.to_call_doc(call_doc)
    caller_payload = await service.build_session(
        call_doc=model,
        viewer_user_id=model.caller_user_id,
        include_ice_servers=include_ice_servers,
    )
    callee_payload = await service.build_session(
        call_doc=model,
        viewer_user_id=model.callee_user_id,
        include_ice_servers=include_ice_servers,
    )

    await emit_to_user(
        sio,
        model.caller_user_id,
        event,
        caller_payload.model_dump(mode="json"),
    )
    await emit_to_user(
        sio,
        model.callee_user_id,
        event,
        callee_payload.model_dump(mode="json"),
    )


async def emit_call_participant_updated_event(
    sio: socketio.AsyncServer,
    *,
    service,
    call_doc: dict[str, Any] | CallDoc,
    actor_user_id: str,
    reason: str,
    skip_actor_socket_id: str | None = None,
) -> None:
    model = service.to_call_doc(call_doc)
    caller_session = await service.build_session(
        call_doc=model,
        viewer_user_id=model.caller_user_id,
        include_ice_servers=False,
    )
    callee_session = await service.build_session(
        call_doc=model,
        viewer_user_id=model.callee_user_id,
        include_ice_servers=False,
    )

    caller_payload = CallParticipantUpdatedEvent(
        call=caller_session.call,
        peer_user=caller_session.peer_user,
        ice_servers=caller_session.ice_servers,
        actor_user_id=actor_user_id,
        reason=reason,
    ).model_dump(mode="json")
    callee_payload = CallParticipantUpdatedEvent(
        call=callee_session.call,
        peer_user=callee_session.peer_user,
        ice_servers=callee_session.ice_servers,
        actor_user_id=actor_user_id,
        reason=reason,
    ).model_dump(mode="json")

    if actor_user_id == model.caller_user_id:
        await sio.emit(
            "call.participant_updated",
            jsonable_encoder(caller_payload),
            room=user_room(model.caller_user_id),
            skip_sid=skip_actor_socket_id,
        )
        await emit_to_user(
            sio,
            model.callee_user_id,
            "call.participant_updated",
            callee_payload,
        )
        return

    await emit_to_user(
        sio,
        model.caller_user_id,
        "call.participant_updated",
        caller_payload,
    )
    await sio.emit(
        "call.participant_updated",
        jsonable_encoder(callee_payload),
        room=user_room(model.callee_user_id),
        skip_sid=skip_actor_socket_id,
    )


async def _emit_history_message_if_any(
    sio: socketio.AsyncServer,
    *,
    history_message,
) -> None:
    if history_message is None:
        return

    await emit_message_to_participants(
        sio,
        sender_id=history_message.sender_id,
        receiver_id=history_message.receiver_id,
        payload=history_message.model_dump(mode="json"),
    )


async def emit_call_recovery_available_event(
    sio: socketio.AsyncServer,
    *,
    service,
    call_doc: dict[str, Any] | CallDoc,
    viewer_user_id: str,
    socket_id: str,
) -> None:
    payload = await service.build_session(
        call_doc=call_doc,
        viewer_user_id=viewer_user_id,
        include_ice_servers=True,
    )
    await sio.emit(
        "call.recovery_available",
        payload.model_dump(mode="json"),
        to=socket_id,
    )


async def handle_call_socket_connect(
    sio: socketio.AsyncServer,
    *,
    sid: str,
    user_id: str,
) -> None:
    service = get_calls_service()
    call_doc = await service.get_active_call(user_id=user_id)
    if call_doc is None:
        return

    model = service.to_call_doc(call_doc)
    if not await _should_offer_recovery(model, user_id=user_id):
        return

    if model.status == "reconnecting":
        schedule_call_reconnect_timeout(
            sio,
            call_id=model.id,
            reconnect_deadline_at=model.reconnect_deadline_at,
        )

    await emit_call_recovery_available_event(
        sio,
        service=service,
        call_doc=model,
        viewer_user_id=user_id,
        socket_id=sid,
    )


async def handle_call_socket_disconnect(
    sio: socketio.AsyncServer,
    *,
    sid: str,
    user_id: str,
) -> None:
    registry = get_call_session_registry()
    unbound_bindings = await registry.unbind_sid(sid)
    if not unbound_bindings:
        return

    service = get_calls_service()
    for binding in unbound_bindings:
        if binding.user_id != user_id or binding.remaining_connection_count > 0:
            continue

        call_doc = await service.mark_reconnecting_from_disconnect(
            user_id=user_id,
            call_id=binding.call_id,
        )
        if call_doc is None:
            continue

        model = service.to_call_doc(call_doc)
        if user_id not in model.disconnected_user_ids:
            continue

        schedule_call_reconnect_timeout(
            sio,
            call_id=model.id,
            reconnect_deadline_at=model.reconnect_deadline_at,
        )
        await emit_call_participant_updated_event(
            sio,
            service=service,
            call_doc=model,
            actor_user_id=user_id,
            reason="disconnected",
        )
        await emit_call_state_event(
            sio,
            event="call.reconnecting",
            service=service,
            call_doc=model,
        )


async def ensure_socket_belongs_to_user(
    sio: socketio.AsyncServer,
    *,
    socket_id: str,
    user_id: str,
) -> None:
    try:
        session = await sio.get_session(socket_id)
    except KeyError as exc:
        raise AppError(
            code="CALL_SOCKET_NOT_FOUND",
            message="Socket connection not found",
            status_code=400,
        ) from exc

    session_user_id = session.get("user_id") if session else None
    if str(session_user_id) != user_id:
        raise AppError(
            code="CALL_SOCKET_FORBIDDEN",
            message="Socket does not belong to the current user",
            status_code=403,
        )


async def join_call_room(
    sio: socketio.AsyncServer,
    *,
    socket_id: str,
    room_id: str,
) -> None:
    await sio.enter_room(socket_id, room_id)


async def bind_call_socket(
    *,
    call_id: str,
    user_id: str,
    socket_id: str,
) -> None:
    registry = get_call_session_registry()
    await registry.bind_socket(call_id=call_id, user_id=user_id, sid=socket_id)


async def clear_call_bindings(
    *,
    call_id: str,
    participant_user_ids: list[str],
) -> None:
    registry = get_call_session_registry()
    await registry.clear_call(
        call_id=call_id, participant_user_ids=participant_user_ids
    )


def schedule_call_expiration(
    sio: socketio.AsyncServer,
    *,
    call_id: str,
    expires_at: datetime | None,
) -> None:
    if expires_at is None:
        return

    cancel_call_expiration(call_id)
    _expiration_tasks[call_id] = asyncio.create_task(
        _expire_call_later(sio=sio, call_id=call_id, expires_at=expires_at)
    )


def cancel_call_expiration(call_id: str) -> None:
    task = _expiration_tasks.pop(call_id, None)
    if task is not None:
        task.cancel()


def schedule_call_reconnect_timeout(
    sio: socketio.AsyncServer,
    *,
    call_id: str,
    reconnect_deadline_at: datetime | None,
) -> None:
    if reconnect_deadline_at is None:
        return

    cancel_call_reconnect_timeout(call_id)
    _reconnect_timeout_tasks[call_id] = asyncio.create_task(
        _expire_reconnecting_call_later(
            sio=sio,
            call_id=call_id,
            reconnect_deadline_at=reconnect_deadline_at,
        )
    )


def cancel_call_reconnect_timeout(call_id: str) -> None:
    task = _reconnect_timeout_tasks.pop(call_id, None)
    if task is not None:
        task.cancel()


async def close_call_runtime() -> None:
    tasks = list(_expiration_tasks.values()) + list(_reconnect_timeout_tasks.values())
    _expiration_tasks.clear()
    _reconnect_timeout_tasks.clear()

    for task in tasks:
        task.cancel()

    for task in tasks:
        with suppress(asyncio.CancelledError):
            await task

    await close_call_session_registry()


async def _bind_socket_to_call_room(
    sio: socketio.AsyncServer,
    *,
    socket_id: str,
    room_id: str,
    call_id: str,
    user_id: str,
) -> None:
    await join_call_room(sio, socket_id=socket_id, room_id=room_id)
    await bind_call_socket(call_id=call_id, user_id=user_id, socket_id=socket_id)


async def _should_offer_recovery(call_doc: CallDoc, *, user_id: str) -> bool:
    if user_id in call_doc.disconnected_user_ids:
        return True

    registry = get_call_session_registry()
    connection_count = await registry.get_connection_count(
        call_id=call_doc.id, user_id=user_id
    )
    return connection_count == 0


async def _expire_call_later(
    *,
    sio: socketio.AsyncServer,
    call_id: str,
    expires_at: datetime,
) -> None:
    delay = max((expires_at - datetime.now(UTC)).total_seconds(), 0)

    try:
        await asyncio.sleep(delay)
        service = get_calls_service()
        result = await service.expire_call_if_due(call_id=call_id)
        if result is not None:
            await clear_call_bindings(
                call_id=call_id,
                participant_user_ids=list(result.call.participant_user_ids),
            )
            await emit_call_state_event(
                sio,
                event="call.ended",
                service=service,
                call_doc=result.call,
            )
            await _emit_history_message_if_any(
                sio,
                history_message=result.history_message,
            )
    except asyncio.CancelledError:
        raise
    finally:
        _expiration_tasks.pop(call_id, None)


async def _expire_reconnecting_call_later(
    *,
    sio: socketio.AsyncServer,
    call_id: str,
    reconnect_deadline_at: datetime,
) -> None:
    delay = max((reconnect_deadline_at - datetime.now(UTC)).total_seconds(), 0)

    try:
        await asyncio.sleep(delay)
        service = get_calls_service()
        result = await service.expire_call_if_due(call_id=call_id)
        if result is not None:
            await clear_call_bindings(
                call_id=call_id,
                participant_user_ids=list(result.call.participant_user_ids),
            )
            await emit_call_state_event(
                sio,
                event="call.ended",
                service=service,
                call_doc=result.call,
            )
            await _emit_history_message_if_any(
                sio,
                history_message=result.history_message,
            )
    except asyncio.CancelledError:
        raise
    finally:
        _reconnect_timeout_tasks.pop(call_id, None)


async def _emit_socket_error(
    sio: socketio.AsyncServer, sid: str, exc: AppError
) -> None:
    await sio.emit(
        "error",
        {"code": exc.code, "message": exc.message},
        to=sid,
    )


async def _emit_invalid_payload(
    sio: socketio.AsyncServer,
    sid: str,
    *,
    message: str,
) -> None:
    await sio.emit(
        "error",
        {"code": "INVALID_PAYLOAD", "message": message},
        to=sid,
    )
