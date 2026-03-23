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

    svc = CallsService(
        repo=repo,
        users_repo=users_repo,
        pings_service=pings_service,
        presence_service=presence_service,
    )
    return svc, repo, users_repo, pings_service, presence_service


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
        "is_live": True,
    }


@pytest.mark.asyncio
async def test_create_call_rejects_self_call(service):
    svc, _, _, _, _ = service

    with pytest.raises(AppError) as exc:
        await svc.create_call(
            caller_user_id="u1",
            callee_user_id="u1",
            call_type="audio",
        )

    assert exc.value.code == "INVALID_CALL_TARGET"


@pytest.mark.asyncio
async def test_create_call_requires_existing_target(service):
    svc, _, users_repo, _, _ = service
    users_repo.find_by_id.return_value = None

    with pytest.raises(AppError) as exc:
        await svc.create_call(
            caller_user_id="u1",
            callee_user_id="u2",
            call_type="audio",
        )

    assert exc.value.code == "USER_NOT_FOUND"


@pytest.mark.asyncio
async def test_create_call_enforces_permission_and_returns_doc(service, ringing_call_doc):
    svc, repo, users_repo, pings_service, _ = service
    users_repo.find_by_id.return_value = {"_id": "u2", "username": "callee"}
    repo.create_call.return_value = ringing_call_doc

    result = await svc.create_call(
        caller_user_id="u1",
        callee_user_id="u2",
        call_type="audio",
    )

    assert result["status"] == "ringing"
    pings_service.ensure_can_message.assert_awaited_once_with(sender_id="u1", receiver_id="u2")
    repo.create_call.assert_awaited_once()


@pytest.mark.asyncio
async def test_end_call_cancels_ringing_call_for_caller(service, ringing_call_doc):
    svc, repo, _, _, _ = service
    cancelled = {**ringing_call_doc, "status": "cancelled", "is_live": False, "ended_at": ringing_call_doc["created_at"]}
    repo.find_by_id.return_value = ringing_call_doc
    repo.expire_call_if_due.return_value = None
    repo.cancel_call.return_value = cancelled

    result = await svc.end_call(user_id="u1", call_id="507f1f77bcf86cd799439011")

    assert result["status"] == "cancelled"
    repo.cancel_call.assert_awaited_once_with(
        call_id="507f1f77bcf86cd799439011",
        caller_user_id="u1",
    )


@pytest.mark.asyncio
async def test_mark_active_promotes_connecting_call(service, ringing_call_doc):
    svc, repo, _, _, _ = service
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
async def test_build_session_generates_turn_credentials(service, ringing_call_doc, monkeypatch):
    svc, _, users_repo, _, presence_service = service
    users_repo.find_by_id.return_value = {
        "_id": "u2",
        "username": "callee",
        "display_name": "Callee",
        "avatar": None,
    }
    presence_service.is_online.return_value = True

    monkeypatch.setattr(settings, "call_stun_urls", ["stun:stun.example.com:3478"])
    monkeypatch.setattr(settings, "call_turn_urls", ["turn:turn.example.com:3478"])
    monkeypatch.setattr(settings, "turn_auth_secret", "secret")
    monkeypatch.setattr(settings, "turn_credential_ttl_seconds", 300)

    session = await svc.build_session(call_doc=ringing_call_doc, viewer_user_id="u1")

    assert session.call.id == "507f1f77bcf86cd799439011"
    assert session.peer_user.id == "u2"
    assert session.peer_user.is_online is True
    assert len(session.ice_servers) == 2
    assert session.ice_servers[1].username is not None
    assert session.ice_servers[1].credential is not None


@pytest.mark.asyncio
async def test_mark_reconnecting_from_disconnect_sets_deadline(service, ringing_call_doc, monkeypatch):
    svc, repo, _, _, _ = service
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
    svc, repo, _, _, _ = service
    repo.expire_call_if_due.return_value = None
    repo.find_by_id.return_value = ringing_call_doc

    with pytest.raises(AppError) as exc:
        await svc.resume_call(user_id="u1", call_id="507f1f77bcf86cd799439011")

    assert exc.value.code == "INVALID_CALL_STATE"


@pytest.mark.asyncio
async def test_resume_call_updates_recoverable_call(service, ringing_call_doc):
    svc, repo, _, _, _ = service
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
    repo.expire_call_if_due.return_value = None
    repo.find_by_id.return_value = reconnecting_call
    repo.resume_reconnecting.return_value = resumed_call

    result = await svc.resume_call(user_id="u1", call_id="507f1f77bcf86cd799439011")

    assert result["disconnected_user_ids"] == []
    repo.resume_reconnecting.assert_awaited_once_with(
        call_id="507f1f77bcf86cd799439011",
        participant_user_id="u1",
    )
