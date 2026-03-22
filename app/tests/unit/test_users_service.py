from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from app.core.errors import AppError
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
        "created_at": now,
        "updated_at": now,
    }


@pytest.fixture
def service():
    users_repo = AsyncMock()
    pings_repo = AsyncMock()
    presence_service = AsyncMock()
    return UsersService(users_repo, pings_repo, presence_service), users_repo, pings_repo, presence_service


@pytest.mark.asyncio
async def test_get_user_profile_returns_minimal_payload_for_self(service, user_doc):
    svc, users_repo, pings_repo, presence_service = service
    users_repo.find_by_id.return_value = user_doc
    presence_service.is_online.return_value = True

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
        "is_online": True,
    }
    pings_repo.has_accepted_permission.assert_not_awaited()
    presence_service.is_online.assert_awaited_once_with(str(user_doc["_id"]))


@pytest.mark.asyncio
async def test_get_user_profile_returns_minimal_payload_for_accepted_ping(service, user_doc):
    svc, users_repo, pings_repo, presence_service = service
    users_repo.find_by_id.return_value = user_doc
    pings_repo.has_accepted_permission.return_value = True
    presence_service.is_online.return_value = False

    result = await svc.get_user_profile(
        current_user_id="viewer-id",
        selected_user_id=str(user_doc["_id"]),
    )

    assert result.id == str(user_doc["_id"])
    assert result.username == "target-user"
    assert result.display_name == "Target User"
    assert result.bio == "Visible profile"
    assert result.is_online is False
    pings_repo.has_accepted_permission.assert_awaited_once_with(
        user_a="viewer-id",
        user_b=str(user_doc["_id"]),
    )
    presence_service.is_online.assert_awaited_once_with(str(user_doc["_id"]))


@pytest.mark.asyncio
async def test_get_user_profile_rejects_without_accepted_ping(service, user_doc):
    svc, users_repo, pings_repo, presence_service = service
    users_repo.find_by_id.return_value = user_doc
    pings_repo.has_accepted_permission.return_value = False

    with pytest.raises(AppError) as exc:
        await svc.get_user_profile(
            current_user_id="viewer-id",
            selected_user_id=str(user_doc["_id"]),
        )

    assert exc.value.code == "PROFILE_ACCESS_FORBIDDEN"
    assert exc.value.status_code == 403
    presence_service.is_online.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_user_profile_rejects_missing_user(service):
    svc, users_repo, pings_repo, presence_service = service
    users_repo.find_by_id.return_value = None

    with pytest.raises(AppError) as exc:
        await svc.get_user_profile(
            current_user_id="viewer-id",
            selected_user_id="507f1f77bcf86cd799439011",
        )

    assert exc.value.code == "USER_NOT_FOUND"
    assert exc.value.status_code == 404
    pings_repo.has_accepted_permission.assert_not_awaited()
    presence_service.is_online.assert_not_awaited()
