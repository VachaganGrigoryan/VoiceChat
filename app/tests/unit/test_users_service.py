from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from app.core.errors import AppError
from app.modules.relationships.schemas import ConnectionState
from app.modules.users.service import UsersService


@pytest.fixture
def user_doc():
    now = datetime(2026, 3, 22, 12, 0, 0, tzinfo=UTC)
    return {
        "_id": "507f1f77bcf86cd799439011",
        "email": "target@test.com",
        "is_verified": True,
        "username": "target-user",
        "display_name": "Target User",
        "bio": "Visible profile",
        "avatar": None,
        "is_private": False,
        "default_discovery_enabled": True,
        "last_seen_at": None,
        "username_updated_at": None,
        "status_emoji": None,
        "status_text": None,
        "status_expires_at": None,
        "pronouns": None,
        "timezone": None,
        "created_at": now,
        "updated_at": now,
    }


@pytest.fixture
def service():
    users_repo = AsyncMock()
    connections = AsyncMock()
    presence_service = AsyncMock()
    return (
        UsersService(users_repo, connections, presence_service),
        users_repo,
        connections,
        presence_service,
    )


@pytest.mark.asyncio
async def test_get_user_profile_returns_minimal_payload_for_self(service, user_doc):
    svc, users_repo, connections, presence_service = service
    users_repo.find_by_id.return_value = user_doc
    presence_service.get_state.return_value = "online"

    result = await svc.get_user_profile(
        current_user_id=str(user_doc["_id"]),
        selected_user_id=str(user_doc["_id"]),
    )

    assert result.model_dump() == {
        "id": str(user_doc["_id"]),
        "username": "target-user",
        "display_name": "Target User",
        "bio": "Visible profile",
        "avatar": None,
        "is_bot": False,
        "main_channel_id": None,
        "status_emoji": None,
        "status_text": None,
        "status_expires_at": None,
        "pronouns": None,
        "timezone": None,
        "is_online": True,
        "presence_state": "online",
        "last_seen_at": None,
        "profile_visibility": "full",
        "relationship": {
            "can_ping": False,
            "chat_allowed": False,
            "connection_status": "none",
            "direction": None,
            "relationship_id": None,
            "blocked_by_me": False,
            "blocks_me": False,
        },
        "connection_timestamp": None,
        "conversation_id": None,
        "shared_conversations": [],
        "shared_spaces": [],
    }
    connections.get_connection_state.assert_not_awaited()
    presence_service.get_state.assert_awaited_once_with(str(user_doc["_id"]))


@pytest.mark.asyncio
async def test_get_user_profile_returns_full_payload_for_active_connection(
    service, user_doc
):
    svc, users_repo, connections, presence_service = service
    users_repo.find_by_id.return_value = user_doc
    connections.get_connection_state.return_value = ConnectionState(
        can_ping=False,
        chat_allowed=True,
        connection_status="active",
    )
    presence_service.get_state.return_value = "offline"

    result = await svc.get_user_profile(
        current_user_id="viewer-id",
        selected_user_id=str(user_doc["_id"]),
    )

    assert result.id == str(user_doc["_id"])
    assert result.username == "target-user"
    assert result.display_name == "Target User"
    assert result.bio == "Visible profile"
    assert result.is_online is False
    assert result.presence_state == "offline"
    assert result.profile_visibility == "full"
    assert result.relationship.chat_allowed is True
    connections.get_connection_state.assert_awaited_once_with(
        viewer_user_id="viewer-id",
        peer_user_id=str(user_doc["_id"]),
    )
    presence_service.get_state.assert_awaited_once_with(str(user_doc["_id"]))


@pytest.mark.asyncio
async def test_get_user_profile_includes_contact_details_when_connected(
    service, user_doc
):
    from app.modules.relationships.schemas import (
        ConnectionExtras,
        SharedConversationSummary,
        SharedSpaceSummary,
    )

    svc, users_repo, connections, presence_service = service
    users_repo.find_by_id.return_value = user_doc
    connections.get_connection_state.return_value = ConnectionState(
        can_ping=False,
        chat_allowed=True,
        connection_status="active",
    )
    connections.get_connection_extras.return_value = ConnectionExtras(
        connection_timestamp=datetime(2026, 3, 20, 10, 0, 0, tzinfo=UTC),
        conversation_id="conv1",
        shared_conversations=[
            SharedConversationSummary(id="c1", type="group", title="Team")
        ],
        shared_spaces=[SharedSpaceSummary(id="s1", name="Acme", slug="acme")],
    )
    presence_service.get_state.return_value = "online"

    result = await svc.get_user_profile(
        current_user_id="viewer-id",
        selected_user_id=str(user_doc["_id"]),
        include={"contact_details"},
    )

    assert result.conversation_id == "conv1"
    assert result.connection_timestamp is not None
    assert [c.id for c in result.shared_conversations] == ["c1"]
    assert [s.id for s in result.shared_spaces] == ["s1"]
    connections.get_connection_extras.assert_awaited_once_with(
        viewer_user_id="viewer-id",
        peer_user_id=str(user_doc["_id"]),
    )


@pytest.mark.asyncio
async def test_get_user_profile_skips_extras_when_not_connected(service, user_doc):
    svc, users_repo, connections, presence_service = service
    users_repo.find_by_id.return_value = user_doc
    connections.get_connection_state.return_value = ConnectionState(
        can_ping=True,
        chat_allowed=False,
        connection_status="none",
    )
    presence_service.get_state.return_value = "offline"

    result = await svc.get_user_profile(
        current_user_id="viewer-id",
        selected_user_id=str(user_doc["_id"]),
        include={"contact_details"},
    )

    assert result.shared_conversations == []
    assert result.shared_spaces == []
    assert result.conversation_id is None
    connections.get_connection_extras.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_user_profile_returns_limited_private_payload_without_connection(
    service, user_doc
):
    svc, users_repo, connections, presence_service = service
    user_doc["is_private"] = True
    users_repo.find_by_id.return_value = user_doc
    connections.get_connection_state.return_value = ConnectionState(
        can_ping=True,
        chat_allowed=False,
        connection_status="none",
    )
    presence_service.get_state.return_value = "online"

    result = await svc.get_user_profile(
        current_user_id="viewer-id",
        selected_user_id=str(user_doc["_id"]),
    )

    assert result.profile_visibility == "limited"
    assert result.bio is None
    assert result.pronouns is None
    assert result.presence_state == "offline"
    assert result.last_seen_at is None
    assert result.relationship.can_ping is True


@pytest.mark.asyncio
async def test_get_user_profile_rejects_missing_user(service):
    svc, users_repo, connections, presence_service = service
    users_repo.find_by_id.return_value = None

    with pytest.raises(AppError) as exc:
        await svc.get_user_profile(
            current_user_id="viewer-id",
            selected_user_id="507f1f77bcf86cd799439011",
        )

    assert exc.value.code == "USER_NOT_FOUND"
    assert exc.value.status_code == 404
    connections.get_connection_state.assert_not_awaited()
    presence_service.get_state.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_user_profile_returns_presence_with_shares_context(service, user_doc):
    svc, users_repo, connections, presence_service = service
    user_doc["is_private"] = True
    users_repo.find_by_id.return_value = user_doc
    connections.get_connection_state.return_value = ConnectionState(
        can_ping=True,
        chat_allowed=False,
        connection_status="none",
    )
    connections.shares_context = AsyncMock(return_value=True)
    presence_service.get_state.return_value = "online"

    result = await svc.get_user_profile(
        current_user_id="viewer-id",
        selected_user_id=str(user_doc["_id"]),
    )

    assert result.profile_visibility == "limited"
    assert result.bio is None
    assert result.presence_state == "online"
