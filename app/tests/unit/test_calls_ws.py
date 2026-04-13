from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from app.modules.calls import ws
from app.modules.calls.service import CallsService


class _FakeSio:
    def __init__(self) -> None:
        self.handlers: dict[str, object] = {}
        self.emitted: list[dict[str, object]] = []

    def on(self, event: str):
        def decorator(func):
            self.handlers[event] = func
            return func

        return decorator

    async def emit(self, event: str, payload, **kwargs) -> None:
        self.emitted.append(
            {
                "event": event,
                "payload": payload,
                **kwargs,
            }
        )


def _build_repo_call(
    *,
    status: str = "accepted",
    join_state: str = "waiting",
    audio_enabled: bool = True,
    video_enabled: bool = False,
) -> dict:
    now = datetime(2026, 4, 13, 12, 0, 0, tzinfo=UTC)
    return {
        "_id": "507f1f77bcf86cd799439011",
        "caller_user_id": "u1",
        "callee_user_id": "u2",
        "participant_user_ids": ["u1", "u2"],
        "type": "audio",
        "status": status,
        "room_id": "call:507f1f77bcf86cd799439011",
        "created_at": now,
        "updated_at": now,
        "answered_at": now,
        "ended_at": None,
        "expires_at": None,
        "reconnect_deadline_at": None,
        "disconnected_user_ids": [],
        "participant_states": {
            "u1": {
                "role": "caller",
                "join_state": join_state,
                "audio_enabled": audio_enabled,
                "video_enabled": video_enabled,
                "updated_at": now,
            },
            "u2": {
                "role": "callee",
                "join_state": "joined",
                "audio_enabled": True,
                "video_enabled": False,
                "updated_at": now,
            },
        },
        "is_live": True,
    }


def _build_service() -> CallsService:
    return CallsService(
        repo=SimpleNamespace(),
        users_repo=SimpleNamespace(),
        pings_service=SimpleNamespace(),
    )


@pytest.mark.asyncio
async def test_call_join_marks_participant_joined_and_emits_update(monkeypatch):
    sio = _FakeSio()
    ws.register_events(sio)
    service = _build_service()
    current_call = _build_repo_call(join_state="waiting")
    joined_call = _build_repo_call(join_state="joined")

    service.get_participant_call = AsyncMock(return_value=current_call)  # type: ignore[attr-defined]
    service.mark_participant_joined = AsyncMock(return_value=joined_call)  # type: ignore[attr-defined]

    bind = AsyncMock()
    participant_updated = AsyncMock()

    monkeypatch.setattr(ws, "get_socket_user_id", AsyncMock(return_value="u1"))
    monkeypatch.setattr(ws, "get_calls_service", lambda: service)
    monkeypatch.setattr(ws, "_bind_socket_to_call_room", bind)
    monkeypatch.setattr(ws, "emit_call_participant_updated_event", participant_updated)

    await sio.handlers["call.join"]("sid-1", {"call_id": "507f1f77bcf86cd799439011"})

    bind.assert_awaited_once()
    service.mark_participant_joined.assert_awaited_once_with(
        user_id="u1",
        call_id="507f1f77bcf86cd799439011",
    )
    participant_updated.assert_awaited_once()


@pytest.mark.asyncio
async def test_call_media_state_skips_emit_when_no_values_change(monkeypatch):
    sio = _FakeSio()
    ws.register_events(sio)
    service = _build_service()
    current_call = _build_repo_call(join_state="joined", audio_enabled=True)

    service.get_participant_call = AsyncMock(return_value=current_call)  # type: ignore[attr-defined]
    service.update_media_state = AsyncMock(return_value=current_call)  # type: ignore[attr-defined]

    bind = AsyncMock()
    participant_updated = AsyncMock()

    monkeypatch.setattr(ws, "get_socket_user_id", AsyncMock(return_value="u1"))
    monkeypatch.setattr(ws, "get_calls_service", lambda: service)
    monkeypatch.setattr(ws, "_bind_socket_to_call_room", bind)
    monkeypatch.setattr(ws, "emit_call_participant_updated_event", participant_updated)

    await sio.handlers["call.media_state"](
        "sid-1",
        {"call_id": "507f1f77bcf86cd799439011", "audio_enabled": True},
    )

    bind.assert_awaited_once()
    service.update_media_state.assert_awaited_once_with(
        user_id="u1",
        call_id="507f1f77bcf86cd799439011",
        audio_enabled=True,
        video_enabled=None,
    )
    participant_updated.assert_not_awaited()


@pytest.mark.asyncio
async def test_call_media_state_emits_update_for_real_change(monkeypatch):
    sio = _FakeSio()
    ws.register_events(sio)
    service = _build_service()
    current_call = _build_repo_call(join_state="joined", audio_enabled=True)
    updated_call = _build_repo_call(join_state="joined", audio_enabled=False)

    service.get_participant_call = AsyncMock(return_value=current_call)  # type: ignore[attr-defined]
    service.update_media_state = AsyncMock(return_value=updated_call)  # type: ignore[attr-defined]

    bind = AsyncMock()
    participant_updated = AsyncMock()

    monkeypatch.setattr(ws, "get_socket_user_id", AsyncMock(return_value="u1"))
    monkeypatch.setattr(ws, "get_calls_service", lambda: service)
    monkeypatch.setattr(ws, "_bind_socket_to_call_room", bind)
    monkeypatch.setattr(ws, "emit_call_participant_updated_event", participant_updated)

    await sio.handlers["call.media_state"](
        "sid-1",
        {"call_id": "507f1f77bcf86cd799439011", "audio_enabled": False},
    )

    participant_updated.assert_awaited_once()


@pytest.mark.asyncio
async def test_call_resume_allows_replacement_socket_and_cancels_timeout(monkeypatch):
    sio = _FakeSio()
    ws.register_events(sio)
    service = _build_service()
    resumed_call = _build_repo_call(status="connecting", join_state="joined")

    service.resume_call = AsyncMock(return_value=resumed_call)  # type: ignore[attr-defined]

    bind = AsyncMock()
    emit_state = AsyncMock()
    participant_updated = AsyncMock()
    cancel_timeout = Mock()
    schedule_timeout = Mock()

    monkeypatch.setattr(ws, "get_socket_user_id", AsyncMock(return_value="u1"))
    monkeypatch.setattr(ws, "get_calls_service", lambda: service)
    monkeypatch.setattr(ws, "_bind_socket_to_call_room", bind)
    monkeypatch.setattr(ws, "emit_call_state_event", emit_state)
    monkeypatch.setattr(ws, "emit_call_participant_updated_event", participant_updated)
    monkeypatch.setattr(ws, "cancel_call_reconnect_timeout", cancel_timeout)
    monkeypatch.setattr(ws, "schedule_call_reconnect_timeout", schedule_timeout)

    await sio.handlers["call.resume"]("sid-2", {"call_id": "507f1f77bcf86cd799439011"})

    service.resume_call.assert_awaited_once_with(
        user_id="u1",
        call_id="507f1f77bcf86cd799439011",
    )
    bind.assert_awaited_once()
    cancel_timeout.assert_called_once_with("507f1f77bcf86cd799439011")
    schedule_timeout.assert_not_called()
    emit_state.assert_awaited_once()
    participant_updated.assert_awaited_once()
