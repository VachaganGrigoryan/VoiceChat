from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from app.core.config import settings
from app.core.errors import AppError
from app.modules.calls.service import CallsService


@pytest.fixture
def service():
    repo = AsyncMock()
    users_repo = AsyncMock()
    pings_service = AsyncMock()
    presence_service = AsyncMock()
    webrtc_service = AsyncMock()
    messages_repo = AsyncMock()

    repo.list_due_call_ids.return_value = []
    repo.expire_call_if_due.return_value = None

    svc = CallsService(
        repo=repo,
        users_repo=users_repo,
        pings_service=pings_service,
        presence_service=presence_service,
        webrtc_service=webrtc_service,
        messages_repo=messages_repo,
    )
    return (
        svc,
        repo,
        users_repo,
        pings_service,
        presence_service,
        webrtc_service,
        messages_repo,
    )


@pytest.fixture
def ringing_call_doc():
    now = datetime(2026, 3, 23, 12, 0, 0, tzinfo=UTC)
    return {
        "_id": "507f1f77bcf86cd799439011",
        "caller_user_id": "u1",
        "callee_user_id": "u2",
        "participant_user_ids": ["u1", "u2"],
        "type": "audio",
        "status": "ringing",
        "room_id": "call:507f1f77bcf86cd799439011",
        "created_at": now,
        "updated_at": now,
        "answered_at": None,
        "ended_at": None,
        "expires_at": now,
        "reconnect_deadline_at": None,
        "disconnected_user_ids": [],
        "participant_states": {
            "u1": {
                "role": "caller",
                "join_state": "waiting",
                "audio_enabled": True,
                "video_enabled": False,
                "updated_at": now,
            },
            "u2": {
                "role": "callee",
                "join_state": "waiting",
                "audio_enabled": True,
                "video_enabled": False,
                "updated_at": now,
            },
        },
        "is_live": True,
    }


@pytest.mark.asyncio
async def test_create_call_rejects_self_call(service):
    svc, _, _, _, _, _, _ = service

    with pytest.raises(AppError) as exc:
        await svc.create_call(
            caller_user_id="u1",
            callee_user_id="u1",
            call_type="audio",
        )

    assert exc.value.code == "INVALID_CALL_TARGET"


@pytest.mark.asyncio
async def test_create_call_requires_existing_target(service):
    svc, _, users_repo, _, _, _, _ = service
    users_repo.find_by_id.return_value = None

    with pytest.raises(AppError) as exc:
        await svc.create_call(
            caller_user_id="u1",
            callee_user_id="u2",
            call_type="audio",
        )

    assert exc.value.code == "USER_NOT_FOUND"


@pytest.mark.asyncio
async def test_create_call_enforces_permission_and_returns_doc(
    service, ringing_call_doc
):
    svc, repo, users_repo, pings_service, _, _, _ = service
    users_repo.find_by_id.return_value = {"_id": "u2", "username": "callee"}
    repo.create_call.return_value = ringing_call_doc

    result = await svc.create_call(
        caller_user_id="u1",
        callee_user_id="u2",
        call_type="audio",
    )

    assert result["status"] == "ringing"
    assert result["participant_states"]["u1"]["audio_enabled"] is True
    assert result["participant_states"]["u2"]["join_state"] == "waiting"
    pings_service.ensure_can_message.assert_awaited_once_with(
        sender_id="u1", receiver_id="u2"
    )
    repo.create_call.assert_awaited_once()


@pytest.mark.asyncio
async def test_accept_call_marks_callee_joined(service, ringing_call_doc):
    svc, repo, _, _, _, _, _ = service
    accepted = {
        **ringing_call_doc,
        "status": "accepted",
        "answered_at": ringing_call_doc["created_at"],
        "expires_at": None,
    }
    accepted_joined = {
        **accepted,
        "participant_states": {
            **accepted["participant_states"],
            "u2": {
                **accepted["participant_states"]["u2"],
                "join_state": "joined",
            },
        },
    }
    repo.find_by_id.return_value = ringing_call_doc
    repo.accept_call.return_value = accepted
    repo.update_participant_state.return_value = accepted_joined

    result = await svc.accept_call(user_id="u2", call_id="507f1f77bcf86cd799439011")

    assert result["participant_states"]["u2"]["join_state"] == "joined"
    repo.update_participant_state.assert_awaited_once_with(
        call_id="507f1f77bcf86cd799439011",
        participant_user_id="u2",
        join_state="joined",
    )


@pytest.mark.asyncio
async def test_end_call_cancels_ringing_call_for_caller(service, ringing_call_doc):
    svc, repo, _, _, _, _, messages_repo = service
    cancelled = {
        **ringing_call_doc,
        "status": "cancelled",
        "is_live": False,
        "ended_at": ringing_call_doc["created_at"],
    }
    call_message = {
        "_id": "507f1f77bcf86cd799439099",
        "conversation_id": "u1_u2",
        "sender_id": "u1",
        "receiver_id": "u2",
        "type": "call",
        "text": None,
        "media": None,
        "call": {
            "call_id": "507f1f77bcf86cd799439011",
            "type": "audio",
            "status": "cancelled",
            "caller_user_id": "u1",
            "callee_user_id": "u2",
            "started_at": ringing_call_doc["created_at"],
            "answered_at": None,
            "ended_at": ringing_call_doc["created_at"],
            "duration_ms": 0,
        },
        "status": "sent",
        "edited_at": None,
        "delivered_at": None,
        "read_at": None,
        "reply_mode": None,
        "reply_to_message_id": None,
        "thread_root_id": None,
        "reply_preview": None,
        "is_thread_root": False,
        "thread_reply_count": 0,
        "last_thread_reply_at": None,
        "reactions": [],
        "hidden_for_user_ids": [],
        "created_at": ringing_call_doc["created_at"],
        "updated_at": ringing_call_doc["created_at"],
    }
    repo.find_by_id.return_value = ringing_call_doc
    repo.cancel_call.return_value = cancelled
    repo.set_history_message_id.return_value = {
        **cancelled,
        "history_message_id": "507f1f77bcf86cd799439099",
    }
    messages_repo.create_call_message.return_value = call_message

    result = await svc.end_call(user_id="u1", call_id="507f1f77bcf86cd799439011")

    assert result.call.status == "cancelled"
    assert result.history_message is not None
    assert result.history_message.type == "call"
    assert result.history_message.call is not None
    assert result.history_message.call.status == "cancelled"
    repo.cancel_call.assert_awaited_once_with(
        call_id="507f1f77bcf86cd799439011",
        caller_user_id="u1",
    )


@pytest.mark.asyncio
async def test_mark_active_promotes_connecting_call(service, ringing_call_doc):
    svc, repo, _, _, _, _, _ = service
    connecting = {
        **ringing_call_doc,
        "status": "connecting",
        "answered_at": ringing_call_doc["created_at"],
        "expires_at": None,
    }
    active = {
        **connecting,
        "status": "active",
    }
    repo.expire_call_if_due.return_value = None
    repo.find_by_id.return_value = connecting
    repo.set_active.return_value = active

    result = await svc.mark_active(user_id="u1", call_id="507f1f77bcf86cd799439011")

    assert result["status"] == "active"
    repo.set_active.assert_awaited_once_with(
        call_id="507f1f77bcf86cd799439011",
        participant_user_id="u1",
    )


@pytest.mark.asyncio
async def test_mark_participant_joined_updates_live_call(service, ringing_call_doc):
    svc, repo, _, _, _, _, _ = service
    accepted = {
        **ringing_call_doc,
        "status": "accepted",
        "answered_at": ringing_call_doc["created_at"],
        "expires_at": None,
    }
    joined = {
        **accepted,
        "participant_states": {
            **accepted["participant_states"],
            "u1": {
                **accepted["participant_states"]["u1"],
                "join_state": "joined",
            },
        },
    }
    repo.find_by_id.return_value = accepted
    repo.update_participant_state.return_value = joined

    result = await svc.mark_participant_joined(
        user_id="u1",
        call_id="507f1f77bcf86cd799439011",
    )

    assert result["participant_states"]["u1"]["join_state"] == "joined"
    repo.update_participant_state.assert_awaited_once_with(
        call_id="507f1f77bcf86cd799439011",
        participant_user_id="u1",
        join_state="joined",
    )


@pytest.mark.asyncio
async def test_update_media_state_updates_audio_flag(service, ringing_call_doc):
    svc, repo, _, _, _, _, _ = service
    active_call = {
        **ringing_call_doc,
        "status": "active",
        "expires_at": None,
        "answered_at": ringing_call_doc["created_at"],
        "participant_states": {
            **ringing_call_doc["participant_states"],
            "u1": {
                **ringing_call_doc["participant_states"]["u1"],
                "join_state": "joined",
            },
            "u2": {
                **ringing_call_doc["participant_states"]["u2"],
                "join_state": "joined",
            },
        },
    }
    updated_call = {
        **active_call,
        "participant_states": {
            **active_call["participant_states"],
            "u1": {
                **active_call["participant_states"]["u1"],
                "audio_enabled": False,
            },
        },
    }
    repo.find_by_id.return_value = active_call
    repo.update_participant_state.return_value = updated_call

    result = await svc.update_media_state(
        user_id="u1",
        call_id="507f1f77bcf86cd799439011",
        audio_enabled=False,
    )

    assert result["participant_states"]["u1"]["audio_enabled"] is False
    repo.update_participant_state.assert_awaited_once_with(
        call_id="507f1f77bcf86cd799439011",
        participant_user_id="u1",
        audio_enabled=False,
    )


@pytest.mark.asyncio
async def test_update_media_state_rejects_video_enable_for_audio_call(
    service, ringing_call_doc
):
    svc, repo, _, _, _, _, _ = service
    active_call = {
        **ringing_call_doc,
        "status": "active",
        "expires_at": None,
        "answered_at": ringing_call_doc["created_at"],
    }
    repo.find_by_id.return_value = active_call

    with pytest.raises(AppError) as exc:
        await svc.update_media_state(
            user_id="u1",
            call_id="507f1f77bcf86cd799439011",
            video_enabled=True,
        )

    assert exc.value.code == "INVALID_CALL_MEDIA_STATE"


@pytest.mark.asyncio
async def test_update_media_state_rejects_terminal_call(service, ringing_call_doc):
    svc, repo, _, _, _, _, _ = service
    ended_call = {
        **ringing_call_doc,
        "status": "ended",
        "ended_at": ringing_call_doc["created_at"],
        "is_live": False,
    }
    repo.find_by_id.return_value = ended_call

    with pytest.raises(AppError) as exc:
        await svc.update_media_state(
            user_id="u1",
            call_id="507f1f77bcf86cd799439011",
            audio_enabled=False,
        )

    assert exc.value.code == "INVALID_CALL_STATE"


@pytest.mark.asyncio
async def test_build_session_uses_webrtc_service_ice_servers(service, ringing_call_doc):
    svc, _, users_repo, _, presence_service, webrtc_service, _ = service
    users_repo.find_by_id.return_value = {
        "_id": "u2",
        "username": "callee",
        "display_name": "Callee",
        "avatar": None,
    }
    presence_service.is_online.return_value = True
    webrtc_service.get_ice_servers.return_value = [
        {"urls": "stun:stun.example.com:3478"},
        {
            "urls": ["turn:turn.example.com:3478"],
            "username": "turn-user",
            "credential": "turn-pass",
        },
    ]

    session = await svc.build_session(call_doc=ringing_call_doc, viewer_user_id="u1")

    assert session.call.id == "507f1f77bcf86cd799439011"
    assert session.peer_user.id == "u2"
    assert session.peer_user.is_online is True
    assert len(session.ice_servers) == 2
    assert session.ice_servers[1].username == "turn-user"
    assert session.ice_servers[1].credential == "turn-pass"
    webrtc_service.get_ice_servers.assert_awaited_once()


@pytest.mark.asyncio
async def test_mark_reconnecting_from_disconnect_sets_deadline(
    service, ringing_call_doc, monkeypatch
):
    svc, repo, _, _, _, _, _ = service
    active_call = {
        **ringing_call_doc,
        "status": "active",
        "expires_at": None,
    }
    reconnecting_call = {
        **active_call,
        "status": "reconnecting",
        "reconnect_deadline_at": datetime.now(UTC),
        "disconnected_user_ids": ["u1"],
    }
    monkeypatch.setattr(settings, "call_reconnect_grace_seconds", 15)
    repo.find_by_id.return_value = active_call
    repo.mark_reconnecting.return_value = reconnecting_call

    result = await svc.mark_reconnecting_from_disconnect(
        user_id="u1",
        call_id="507f1f77bcf86cd799439011",
    )

    assert result["status"] == "reconnecting"
    repo.mark_reconnecting.assert_awaited_once()
    deadline = repo.mark_reconnecting.await_args.kwargs["reconnect_deadline_at"]
    assert deadline > datetime.now(UTC)


@pytest.mark.asyncio
async def test_resume_call_rejects_non_recoverable_state(service, ringing_call_doc):
    svc, repo, _, _, _, _, _ = service
    repo.find_by_id.return_value = ringing_call_doc

    with pytest.raises(AppError) as exc:
        await svc.resume_call(user_id="u1", call_id="507f1f77bcf86cd799439011")

    assert exc.value.code == "INVALID_CALL_STATE"


@pytest.mark.asyncio
async def test_resume_call_updates_recoverable_call(service, ringing_call_doc):
    svc, repo, _, _, _, _, _ = service
    reconnecting_call = {
        **ringing_call_doc,
        "status": "reconnecting",
        "expires_at": None,
        "reconnect_deadline_at": datetime.now(UTC),
        "disconnected_user_ids": ["u1"],
    }
    resumed_call = {
        **reconnecting_call,
        "disconnected_user_ids": [],
    }
    repo.find_by_id.return_value = reconnecting_call
    repo.resume_reconnecting.return_value = resumed_call

    result = await svc.resume_call(user_id="u1", call_id="507f1f77bcf86cd799439011")

    assert result["disconnected_user_ids"] == []
    repo.resume_reconnecting.assert_awaited_once_with(
        call_id="507f1f77bcf86cd799439011",
        participant_user_id="u1",
    )


@pytest.mark.asyncio
async def test_resume_call_preserves_media_state(service, ringing_call_doc):
    svc, repo, _, _, _, _, _ = service
    reconnecting_call = {
        **ringing_call_doc,
        "status": "reconnecting",
        "expires_at": None,
        "reconnect_deadline_at": datetime.now(UTC),
        "disconnected_user_ids": ["u1"],
        "participant_states": {
            **ringing_call_doc["participant_states"],
            "u1": {
                **ringing_call_doc["participant_states"]["u1"],
                "join_state": "disconnected",
                "audio_enabled": False,
            },
            "u2": {
                **ringing_call_doc["participant_states"]["u2"],
                "join_state": "joined",
            },
        },
    }
    resumed_call = {
        **reconnecting_call,
        "disconnected_user_ids": [],
        "participant_states": {
            **reconnecting_call["participant_states"],
            "u1": {
                **reconnecting_call["participant_states"]["u1"],
                "join_state": "joined",
            },
        },
    }
    repo.find_by_id.return_value = reconnecting_call
    repo.resume_reconnecting.return_value = resumed_call

    result = await svc.resume_call(user_id="u1", call_id="507f1f77bcf86cd799439011")

    assert result["participant_states"]["u1"]["audio_enabled"] is False
    assert result["participant_states"]["u1"]["join_state"] == "joined"


def test_to_call_doc_infers_participant_states_for_legacy_call(service):
    svc, _, _, _, _, _, _ = service
    now = datetime(2026, 3, 23, 12, 0, 0, tzinfo=UTC)
    legacy_call = {
        "_id": "507f1f77bcf86cd799439011",
        "caller_user_id": "u1",
        "callee_user_id": "u2",
        "participant_user_ids": ["u1", "u2"],
        "type": "video",
        "status": "accepted",
        "room_id": "call:507f1f77bcf86cd799439011",
        "created_at": now,
        "updated_at": now,
        "answered_at": now,
        "ended_at": None,
        "expires_at": None,
        "reconnect_deadline_at": None,
        "disconnected_user_ids": [],
        "is_live": True,
    }

    model = svc.to_call_doc(legacy_call)

    assert model.participant_states["u1"].join_state == "waiting"
    assert model.participant_states["u2"].join_state == "joined"
    assert model.participant_states["u1"].video_enabled is True


@pytest.mark.asyncio
async def test_list_history_builds_peer_direction_duration_and_message_id(
    service, ringing_call_doc
):
    svc, repo, users_repo, _, presence_service, _, _ = service
    terminal_call = {
        **ringing_call_doc,
        "status": "ended",
        "answered_at": datetime(2026, 3, 23, 12, 1, 0, tzinfo=UTC),
        "ended_at": datetime(2026, 3, 23, 12, 2, 30, tzinfo=UTC),
        "expires_at": None,
        "is_live": False,
        "history_message_id": "507f1f77bcf86cd799439099",
    }
    repo.list_history.return_value = ([terminal_call], "next-cursor")
    users_repo.find_by_ids.return_value = {
        "u2": {
            "_id": "u2",
            "username": "callee",
            "display_name": "Callee",
            "avatar": None,
        }
    }
    presence_service.is_online.return_value = True

    items, next_cursor = await svc.list_history(user_id="u1", limit=20)

    assert next_cursor == "next-cursor"
    assert len(items) == 1
    item = items[0]
    assert item.direction == "outgoing"
    assert item.peer_user.id == "u2"
    assert item.peer_user.is_online is True
    assert item.duration_ms == 90000
    assert item.message_id == "507f1f77bcf86cd799439099"
