from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.core.errors import AppError
from app.modules.pings.schemas import PingListResponse, PingResponse
from app.modules.pings.service import PingsService


@pytest.fixture
def service():
    pings_repo = AsyncMock()
    users_repo = AsyncMock()
    presence_service = AsyncMock()

    svc = PingsService(
        pings_repo=pings_repo,
        users_repo=users_repo,
        presence_service=presence_service,
    )
    return svc, pings_repo, users_repo, presence_service


@pytest.fixture
def fixed_now():
    return datetime(2026, 3, 14, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
def pending_ping_doc(fixed_now):
    return {
        "_id": "ping1",
        "from_user_id": "u1",
        "to_user_id": "u2",
        "status": "pending",
        "created_at": fixed_now,
        "updated_at": fixed_now,
        "responded_at": None,
    }


@pytest.fixture
def accepted_ping_doc(fixed_now):
    return {
        "_id": "ping1",
        "from_user_id": "u1",
        "to_user_id": "u2",
        "status": "accepted",
        "created_at": fixed_now,
        "updated_at": fixed_now,
        "responded_at": fixed_now,
    }


@pytest.mark.asyncio
async def test_send_ping_rejects_self_ping(service):
    svc, _, _, _ = service

    with pytest.raises(HTTPException) as exc:
        await svc.send_ping(from_user_id="u1", to_user_id="u1")

    assert exc.value.status_code == 400
    assert exc.value.detail == "Cannot ping yourself"


@pytest.mark.asyncio
async def test_send_ping_rejects_missing_target(service):
    svc, _, users_repo, _ = service
    users_repo.find_by_id.return_value = None

    with pytest.raises(HTTPException) as exc:
        await svc.send_ping(from_user_id="u1", to_user_id="u2")

    assert exc.value.status_code == 404
    assert exc.value.detail == "User not found"


@pytest.mark.asyncio
async def test_send_ping_rejects_when_already_accepted(service):
    svc, pings_repo, users_repo, _ = service

    users_repo.find_by_id.return_value = {"_id": "u2", "username": "target"}
    pings_repo.find_by_pair_id.return_value = {
        "_id": "ping1",
        "from_user_id": "u2",
        "to_user_id": "u1",
        "status": "accepted",
    }

    with pytest.raises(HTTPException) as exc:
        await svc.send_ping(from_user_id="u1", to_user_id="u2")

    assert exc.value.status_code == 409
    assert exc.value.detail == "Chat permission already granted"


@pytest.mark.asyncio
async def test_send_ping_rejects_when_already_pending(service):
    svc, pings_repo, users_repo, _ = service

    users_repo.find_by_id.return_value = {"_id": "u2", "username": "target"}
    pings_repo.find_by_pair_id.return_value = {
        "_id": "ping1",
        "from_user_id": "u1",
        "to_user_id": "u2",
        "status": "pending",
    }

    with pytest.raises(HTTPException) as exc:
        await svc.send_ping(from_user_id="u1", to_user_id="u2")

    assert exc.value.status_code == 409
    assert exc.value.detail == "Ping already pending"


@pytest.mark.asyncio
async def test_send_ping_creates_pending_ping(service, pending_ping_doc):
    svc, pings_repo, users_repo, _ = service

    users_repo.find_by_id.return_value = {"_id": "u2", "username": "target"}
    pings_repo.find_by_pair_id.return_value = None
    pings_repo.create_ping.return_value = pending_ping_doc

    result = await svc.send_ping(from_user_id="u1", to_user_id="u2")

    assert isinstance(result, PingResponse)
    assert result.id == "ping1"
    assert result.status == "pending"
    pings_repo.create_ping.assert_awaited_once_with(from_user_id="u1", to_user_id="u2")


@pytest.mark.asyncio
async def test_accept_ping_rejects_missing_ping(service):
    svc, pings_repo, _, _ = service
    pings_repo.find_by_id.return_value = None

    with pytest.raises(HTTPException) as exc:
        await svc.accept_ping(user_id="u2", ping_id="ping1")

    assert exc.value.status_code == 404
    assert exc.value.detail == "Ping not found"


@pytest.mark.asyncio
async def test_accept_ping_rejects_non_recipient(service, pending_ping_doc):
    svc, pings_repo, _, _ = service
    pings_repo.find_by_id.return_value = pending_ping_doc

    with pytest.raises(HTTPException) as exc:
        await svc.accept_ping(user_id="u3", ping_id="ping1")

    assert exc.value.status_code == 403
    assert exc.value.detail == "Not allowed to accept this ping"


@pytest.mark.asyncio
async def test_accept_ping_rejects_non_pending(service, accepted_ping_doc):
    svc, pings_repo, _, _ = service
    pings_repo.find_by_id.return_value = accepted_ping_doc

    with pytest.raises(HTTPException) as exc:
        await svc.accept_ping(user_id="u2", ping_id="ping1")

    assert exc.value.status_code == 409
    assert exc.value.detail == "Ping is not pending"


@pytest.mark.asyncio
async def test_accept_ping_success(service, pending_ping_doc, accepted_ping_doc):
    svc, pings_repo, _, _ = service
    pings_repo.find_by_id.return_value = pending_ping_doc
    pings_repo.update_status.return_value = accepted_ping_doc

    result = await svc.accept_ping(user_id="u2", ping_id="ping1")

    assert isinstance(result, PingResponse)
    assert result.status == "accepted"
    pings_repo.update_status.assert_awaited_once_with(ping_id="ping1", status="accepted")


@pytest.mark.asyncio
async def test_decline_ping_rejects_missing_ping(service):
    svc, pings_repo, _, _ = service
    pings_repo.find_by_id.return_value = None

    with pytest.raises(HTTPException) as exc:
        await svc.decline_ping(user_id="u2", ping_id="ping1")

    assert exc.value.status_code == 404
    assert exc.value.detail == "Ping not found"


@pytest.mark.asyncio
async def test_decline_ping_rejects_non_recipient(service, pending_ping_doc):
    svc, pings_repo, _, _ = service
    pings_repo.find_by_id.return_value = pending_ping_doc

    with pytest.raises(HTTPException) as exc:
        await svc.decline_ping(user_id="u3", ping_id="ping1")

    assert exc.value.status_code == 403
    assert exc.value.detail == "Not allowed to decline this ping"


@pytest.mark.asyncio
async def test_decline_ping_rejects_non_pending(service, accepted_ping_doc):
    svc, pings_repo, _, _ = service
    pings_repo.find_by_id.return_value = accepted_ping_doc

    with pytest.raises(HTTPException) as exc:
        await svc.decline_ping(user_id="u2", ping_id="ping1")

    assert exc.value.status_code == 409
    assert exc.value.detail == "Ping is not pending"


@pytest.mark.asyncio
async def test_decline_ping_success(service, pending_ping_doc, fixed_now):
    svc, pings_repo, _, _ = service
    declined_doc = {
        **pending_ping_doc,
        "status": "declined",
        "responded_at": fixed_now,
    }
    pings_repo.find_by_id.return_value = pending_ping_doc
    pings_repo.update_status.return_value = declined_doc

    result = await svc.decline_ping(user_id="u2", ping_id="ping1")

    assert isinstance(result, PingResponse)
    assert result.status == "declined"
    pings_repo.update_status.assert_awaited_once_with(ping_id="ping1", status="declined")


@pytest.mark.asyncio
async def test_list_incoming_returns_items_with_peer_and_presence(service, pending_ping_doc):
    svc, pings_repo, users_repo, presence_service = service

    pings_repo.list_incoming.return_value = [pending_ping_doc]
    users_repo.find_by_id.return_value = {
        "_id": "u1",
        "username": "alice",
        "display_name": "Alice",
        "avatar": {"url": "avatar.png"},
    }
    presence_service.is_online.return_value = True

    result = await svc.list_incoming(user_id="u2", limit=20)

    assert isinstance(result, PingListResponse)
    assert len(result.items) == 1
    assert result.items[0].ping.id == "ping1"
    assert result.items[0].peer.id == "u1"
    assert result.items[0].peer.username == "alice"
    assert result.items[0].peer.is_online is True


@pytest.mark.asyncio
async def test_list_outgoing_returns_items_with_peer_and_presence(service, pending_ping_doc):
    svc, pings_repo, users_repo, presence_service = service

    pings_repo.list_outgoing.return_value = [pending_ping_doc]
    users_repo.find_by_id.return_value = {
        "_id": "u2",
        "username": "bob",
        "display_name": "Bob",
        "avatar": None,
    }
    presence_service.is_online.return_value = False

    result = await svc.list_outgoing(user_id="u1", limit=20)

    assert isinstance(result, PingListResponse)
    assert len(result.items) == 1
    assert result.items[0].peer.id == "u2"
    assert result.items[0].peer.username == "bob"
    assert result.items[0].peer.is_online is False


@pytest.mark.asyncio
async def test_list_incoming_handles_missing_peer(service, pending_ping_doc):
    svc, pings_repo, users_repo, presence_service = service

    pings_repo.list_incoming.return_value = [pending_ping_doc]
    users_repo.find_by_id.return_value = None
    presence_service.is_online.return_value = False

    result = await svc.list_incoming(user_id="u2", limit=20)

    assert len(result.items) == 1
    assert result.items[0].peer.id == "u1"
    assert result.items[0].peer.username == ""
    assert result.items[0].peer.display_name is None
    assert result.items[0].peer.avatar is None
    assert result.items[0].peer.is_online is False


@pytest.mark.asyncio
async def test_list_outgoing_without_presence_service_defaults_false(pending_ping_doc):
    pings_repo = AsyncMock()
    users_repo = AsyncMock()

    svc = PingsService(
        pings_repo=pings_repo,
        users_repo=users_repo,
        presence_service=None,
    )

    pings_repo.list_outgoing.return_value = [pending_ping_doc]
    users_repo.find_by_id.return_value = {
        "_id": "u2",
        "username": "bob",
        "display_name": "Bob",
        "avatar": None,
    }

    result = await svc.list_outgoing(user_id="u1", limit=20)

    assert len(result.items) == 1
    assert result.items[0].peer.is_online is False


@pytest.mark.asyncio
async def test_has_chat_permission_true(service):
    svc, pings_repo, _, _ = service
    pings_repo.has_accepted_permission.return_value = True

    result = await svc.has_chat_permission(user_a="u1", user_b="u2")

    assert result is True
    pings_repo.has_accepted_permission.assert_awaited_once_with(user_a="u1", user_b="u2")


@pytest.mark.asyncio
async def test_has_chat_permission_false(service):
    svc, pings_repo, _, _ = service
    pings_repo.has_accepted_permission.return_value = False

    result = await svc.has_chat_permission(user_a="u1", user_b="u2")

    assert result is False


@pytest.mark.asyncio
async def test_ensure_can_message_allows_when_permission_exists(service):
    svc, pings_repo, _, _ = service
    pings_repo.has_accepted_permission.return_value = True

    await svc.ensure_can_message(sender_id="u1", receiver_id="u2")

    pings_repo.has_accepted_permission.assert_awaited_once_with(user_a="u1", user_b="u2")


@pytest.mark.asyncio
async def test_ensure_can_message_raises_when_permission_missing(service):
    svc, pings_repo, _, _ = service
    pings_repo.has_accepted_permission.return_value = False

    with pytest.raises(AppError) as exc:
        await svc.ensure_can_message(sender_id="u1", receiver_id="u2")

    assert exc.value.code == "CHAT_PERMISSION_REQUIRED"
    assert exc.value.status_code == 403


def test_to_ping_response(service, pending_ping_doc):
    svc, _, _, _ = service

    result = svc._to_ping_response(pending_ping_doc)

    assert isinstance(result, PingResponse)
    assert result.id == "ping1"
    assert result.from_user_id == "u1"
    assert result.to_user_id == "u2"
    assert result.status == "pending"