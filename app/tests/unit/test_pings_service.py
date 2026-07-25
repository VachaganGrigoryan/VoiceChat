from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.core.errors import AppError
from app.modules.pings.schemas import PingResponse
from app.modules.pings.service import PingsService


@pytest.fixture
def service():
    pings_repo = AsyncMock()
    users_repo = AsyncMock()
    presence_service = AsyncMock()
    pings_repo.is_blocked.return_value = False

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


@pytest.fixture
def declined_ping_doc(fixed_now):
    return {
        "_id": "ping1",
        "from_user_id": "u1",
        "to_user_id": "u2",
        "status": "declined",
        "created_at": fixed_now,
        "updated_at": fixed_now,
        "responded_at": fixed_now,
    }


@pytest.fixture
def cancelled_ping_doc(fixed_now):
    return {
        "_id": "ping1",
        "from_user_id": "u1",
        "to_user_id": "u2",
        "status": "cancelled",
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
async def test_send_ping_returns_existing_when_already_pending(service, pending_ping_doc):
    svc, pings_repo, users_repo, _ = service

    users_repo.find_by_id.return_value = {"_id": "u2", "username": "target"}
    existing_ping = pending_ping_doc
    pings_repo.find_by_pair_id.return_value = existing_ping

    result = await svc.send_ping(from_user_id="u1", to_user_id="u2")

    assert isinstance(result, PingResponse)
    assert result.id == "ping1"
    assert result.status == "pending"
    pings_repo.create_ping.assert_not_awaited()


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
async def test_send_ping_reopens_declined_ping(service, declined_ping_doc, pending_ping_doc):
    svc, pings_repo, users_repo, _ = service

    users_repo.find_by_id.return_value = {"_id": "u2", "username": "target"}
    pings_repo.find_by_pair_id.return_value = declined_ping_doc
    pings_repo.reopen_ping.return_value = {
        **pending_ping_doc,
        "_id": "ping1",
        "from_user_id": "u1",
        "to_user_id": "u2",
    }

    result = await svc.send_ping(from_user_id="u1", to_user_id="u2")

    assert isinstance(result, PingResponse)
    assert result.id == "ping1"
    assert result.status == "pending"
    pings_repo.reopen_ping.assert_awaited_once_with(
        ping_id="ping1",
        from_user_id="u1",
        to_user_id="u2",
    )
    pings_repo.create_ping.assert_not_awaited()


@pytest.mark.asyncio
async def test_send_ping_reopens_cancelled_ping_with_new_direction(service, cancelled_ping_doc, pending_ping_doc):
    svc, pings_repo, users_repo, _ = service

    users_repo.find_by_id.return_value = {"_id": "u1", "username": "target"}
    pings_repo.find_by_pair_id.return_value = cancelled_ping_doc
    pings_repo.reopen_ping.return_value = {
        **pending_ping_doc,
        "_id": "ping1",
        "from_user_id": "u2",
        "to_user_id": "u1",
    }

    result = await svc.send_ping(from_user_id="u2", to_user_id="u1")

    assert isinstance(result, PingResponse)
    assert result.id == "ping1"
    assert result.from_user_id == "u2"
    assert result.to_user_id == "u1"
    assert result.status == "pending"
    pings_repo.reopen_ping.assert_awaited_once_with(
        ping_id="ping1",
        from_user_id="u2",
        to_user_id="u1",
    )
    pings_repo.create_ping.assert_not_awaited()


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

    pings_repo.list_incoming.return_value = ([pending_ping_doc], None)
    users_repo.find_by_id.return_value = {
        "_id": "u1",
        "username": "alice",
        "display_name": "Alice",
        "avatar": {
            "storage": "local",
            "key": "avatars/alice.png",
            "url": "https://stale.example.com/alice.png",
        },
    }
    presence_service.is_online.return_value = True

    items, next_cursor = await svc.list_incoming(user_id="u2", limit=20)

    assert next_cursor is None
    assert len(items) == 1
    assert items[0].ping.id == "ping1"
    assert items[0].peer.id == "u1"
    assert items[0].peer.username == "alice"
    assert items[0].peer.avatar == {
        "storage": "local",
        "key": "avatars/alice.png",
        "url": "/media/avatars/alice.png",
    }
    assert items[0].peer.is_online is True


@pytest.mark.asyncio
async def test_list_outgoing_returns_items_with_peer_and_presence(service, pending_ping_doc):
    svc, pings_repo, users_repo, presence_service = service

    pings_repo.list_outgoing.return_value = ([pending_ping_doc], None)
    users_repo.find_by_id.return_value = {
        "_id": "u2",
        "username": "bob",
        "display_name": "Bob",
        "avatar": None,
    }
    presence_service.is_online.return_value = False

    items, next_cursor = await svc.list_outgoing(user_id="u1", limit=20)

    assert next_cursor is None
    assert len(items) == 1
    assert items[0].peer.id == "u2"
    assert items[0].peer.username == "bob"
    assert items[0].peer.is_online is False


@pytest.mark.asyncio
async def test_list_incoming_handles_missing_peer(service, pending_ping_doc):
    svc, pings_repo, users_repo, presence_service = service

    pings_repo.list_incoming.return_value = ([pending_ping_doc], None)
    users_repo.find_by_id.return_value = None
    presence_service.is_online.return_value = False

    items, _ = await svc.list_incoming(user_id="u2", limit=20)

    assert len(items) == 1
    assert items[0].peer.id == "u1"
    assert items[0].peer.username == ""
    assert items[0].peer.display_name is None
    assert items[0].peer.avatar is None
    assert items[0].peer.is_online is False


@pytest.mark.asyncio
async def test_list_outgoing_without_presence_service_defaults_false(pending_ping_doc):
    pings_repo = AsyncMock()
    users_repo = AsyncMock()
    pings_repo.is_blocked.return_value = False

    svc = PingsService(
        pings_repo=pings_repo,
        users_repo=users_repo,
        presence_service=None,
    )

    pings_repo.list_outgoing.return_value = ([pending_ping_doc], None)
    users_repo.find_by_id.return_value = {
        "_id": "u2",
        "username": "bob",
        "display_name": "Bob",
        "avatar": None,
    }

    items, _ = await svc.list_outgoing(user_id="u1", limit=20)

    assert len(items) == 1
    assert items[0].peer.is_online is False


@pytest.mark.asyncio
async def test_to_realtime_payload_builds_avatar_url(service, pending_ping_doc):
    svc, _, users_repo, presence_service = service
    users_repo.find_by_id.return_value = {
        "_id": "u1",
        "username": "alice",
        "display_name": "Alice",
        "avatar": {
            "storage": "local",
            "key": "avatars/alice.png",
            "url": "https://stale.example.com/alice.png",
        },
    }
    presence_service.is_online.return_value = True

    payload = await svc.to_realtime_payload(pending_ping_doc, incoming_for="u2")

    assert payload["peer"]["avatar"] == {
        "storage": "local",
        "key": "avatars/alice.png",
        "url": "/media/avatars/alice.png",
    }
    assert payload["peer"]["is_online"] is True


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
    pings_repo.is_blocked.return_value = False
    pings_repo.has_accepted_permission.return_value = True

    await svc.ensure_can_message(sender_id="u1", receiver_id="u2")

    pings_repo.is_blocked.assert_awaited_once_with(user_a="u1", user_b="u2")
    pings_repo.has_accepted_permission.assert_awaited_once_with(user_a="u1", user_b="u2")


@pytest.mark.asyncio
async def test_ensure_can_message_raises_when_permission_missing(service):
    svc, pings_repo, _, _ = service
    pings_repo.is_blocked.return_value = False
    pings_repo.has_accepted_permission.return_value = False

    with pytest.raises(AppError) as exc:
        await svc.ensure_can_message(sender_id="u1", receiver_id="u2")

    assert exc.value.code == "CHAT_PERMISSION_REQUIRED"
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_ensure_can_message_raises_when_blocked(service):
    svc, pings_repo, _, _ = service
    pings_repo.is_blocked.return_value = True

    with pytest.raises(AppError) as exc:
        await svc.ensure_can_message(sender_id="u1", receiver_id="u2")

    assert exc.value.code == "CHAT_BLOCKED"
    assert exc.value.status_code == 403


def test_to_ping_response(service, pending_ping_doc):
    svc, _, _, _ = service

    result = svc._to_ping_response(pending_ping_doc)

    assert isinstance(result, PingResponse)
    assert result.id == "ping1"
    assert result.from_user_id == "u1"
    assert result.to_user_id == "u2"
    assert result.status == "pending"


@pytest.fixture
def notifying_service():
    pings_repo = AsyncMock()
    users_repo = AsyncMock()
    notifications_service = AsyncMock()
    pings_repo.is_blocked.return_value = False

    svc = PingsService(
        pings_repo=pings_repo,
        users_repo=users_repo,
        notifications_service=notifications_service,
    )
    return svc, pings_repo, users_repo, notifications_service


@pytest.mark.asyncio
async def test_send_ping_emits_ping_received_notification(
    notifying_service, pending_ping_doc
):
    svc, pings_repo, users_repo, notifications = notifying_service
    users_repo.find_by_id.return_value = {"_id": "u2", "username": "target"}
    pings_repo.find_by_pair_id.return_value = None
    pings_repo.create_ping.return_value = pending_ping_doc

    await svc.send_ping(from_user_id="u1", to_user_id="u2")

    notifications.create_notification.assert_awaited_once_with(
        user_id="u2",
        kind="ping_received",
        source_type="ping",
        data={"peer_user_id": "u1"},
    )


@pytest.mark.asyncio
async def test_accept_ping_emits_notification_to_sender(
    notifying_service, pending_ping_doc, accepted_ping_doc
):
    svc, pings_repo, _, notifications = notifying_service
    pings_repo.find_by_id.return_value = pending_ping_doc
    pings_repo.update_status.return_value = accepted_ping_doc

    await svc.accept_ping(user_id="u2", ping_id="ping1")

    notifications.create_notification.assert_awaited_once_with(
        user_id="u1",
        kind="ping_accepted",
        source_type="ping",
        data={"peer_user_id": "u2"},
    )


@pytest.mark.asyncio
async def test_decline_ping_emits_notification_to_sender(
    notifying_service, pending_ping_doc
):
    svc, pings_repo, _, notifications = notifying_service
    pings_repo.find_by_id.return_value = pending_ping_doc
    pings_repo.update_status.return_value = {
        **pending_ping_doc,
        "status": "declined",
    }

    await svc.decline_ping(user_id="u2", ping_id="ping1")

    notifications.create_notification.assert_awaited_once_with(
        user_id="u1",
        kind="ping_declined",
        source_type="ping",
        data={"peer_user_id": "u2"},
    )


@pytest.mark.asyncio
async def test_cancel_ping_emits_notification_to_receiver(
    notifying_service, pending_ping_doc
):
    svc, pings_repo, _, notifications = notifying_service
    pings_repo.find_by_id.return_value = pending_ping_doc
    pings_repo.update_status.return_value = {
        **pending_ping_doc,
        "status": "cancelled",
    }

    await svc.cancel_ping(user_id="u1", ping_id="ping1")

    notifications.create_notification.assert_awaited_once_with(
        user_id="u2",
        kind="ping_cancelled",
        source_type="ping",
        data={"peer_user_id": "u1"},
    )


@pytest.mark.asyncio
async def test_block_user_notifies_only_blocker(notifying_service, accepted_ping_doc):
    svc, pings_repo, _, notifications = notifying_service
    pings_repo.block_pair.return_value = {
        **accepted_ping_doc,
        "status": "blocked",
    }

    await svc.block_user(user_id="u1", peer_user_id="u2")

    notifications.create_notification.assert_awaited_once_with(
        user_id="u1",
        kind="user_blocked",
        source_type="ping",
        data={"peer_user_id": "u2"},
    )


@pytest.mark.asyncio
async def test_get_contact_extras_self_returns_empty(service):
    svc, pings_repo, _, _ = service

    extras = await svc.get_contact_extras(viewer_user_id="u1", peer_user_id="u1")

    assert extras.connection_timestamp is None
    assert extras.conversation_id is None
    assert extras.shared_conversations == []
    assert extras.shared_spaces == []
    pings_repo.get_pair_state.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_contact_extras_non_accepted_returns_empty(service, pending_ping_doc):
    svc, pings_repo, _, _ = service
    pings_repo.get_pair_state.return_value = pending_ping_doc

    extras = await svc.get_contact_extras(viewer_user_id="u2", peer_user_id="u1")

    assert extras.connection_timestamp is None
    assert extras.shared_conversations == []
    assert extras.shared_spaces == []


@pytest.mark.asyncio
async def test_get_contact_extras_aggregates(service, accepted_ping_doc, monkeypatch):
    svc, pings_repo, _, _ = service
    pings_repo.get_pair_state.return_value = accepted_ping_doc

    from app.modules.pings.schemas import (
        SharedConversationSummary,
        SharedSpaceSummary,
    )

    async def fake_conversations(**_kwargs):
        return [SharedConversationSummary(id="c1", type="group", title="Team")]

    async def fake_spaces(**_kwargs):
        return [SharedSpaceSummary(id="s1", name="Acme", slug="acme")]

    monkeypatch.setattr(svc, "_shared_conversations", fake_conversations)
    monkeypatch.setattr(svc, "_shared_spaces", fake_spaces)

    extras = await svc.get_contact_extras(viewer_user_id="u2", peer_user_id="u1")

    assert extras.connection_timestamp is not None
    assert [c.id for c in extras.shared_conversations] == ["c1"]
    assert [s.id for s in extras.shared_spaces] == ["s1"]


@pytest.mark.asyncio
async def test_shares_context_true_due_to_shared_conversations(service, monkeypatch):
    svc, pings_repo, _, _ = service

    async def fake_conversations(**_kwargs):
        return [1]  # non-empty

    async def fake_spaces(**_kwargs):
        return []

    monkeypatch.setattr(svc, "_shared_conversations", fake_conversations)
    monkeypatch.setattr(svc, "_shared_spaces", fake_spaces)

    res = await svc.shares_context(viewer_user_id="u2", peer_user_id="u1")
    assert res is True


@pytest.mark.asyncio
async def test_shares_context_true_due_to_shared_spaces(service, monkeypatch):
    svc, pings_repo, _, _ = service

    async def fake_conversations(**_kwargs):
        return []

    async def fake_spaces(**_kwargs):
        return [1]  # non-empty

    monkeypatch.setattr(svc, "_shared_conversations", fake_conversations)
    monkeypatch.setattr(svc, "_shared_spaces", fake_spaces)

    res = await svc.shares_context(viewer_user_id="u2", peer_user_id="u1")
    assert res is True


@pytest.mark.asyncio
async def test_shares_context_false_when_none_shared(service, monkeypatch):
    svc, pings_repo, _, _ = service

    async def fake_conversations(**_kwargs):
        return []

    async def fake_spaces(**_kwargs):
        return []

    monkeypatch.setattr(svc, "_shared_conversations", fake_conversations)
    monkeypatch.setattr(svc, "_shared_spaces", fake_spaces)

    res = await svc.shares_context(viewer_user_id="u2", peer_user_id="u1")
    assert res is False
