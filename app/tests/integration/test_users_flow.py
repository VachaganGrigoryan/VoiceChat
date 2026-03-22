from __future__ import annotations

import uuid

import pytest

from app.modules.auth import service as auth_service
from app.tests.integration.test_realtime_socket import (
    _create_verified_user_and_tokens,
    _grant_chat_permission,
)


@pytest.mark.asyncio
async def test_get_me_after_verify(inprocess_client, monkeypatch):
    email = f"user-{uuid.uuid4().hex[:8]}@test.com"
    fixed_code = "123456"

    monkeypatch.setattr(auth_service, "_generate_6_digit_code", lambda: fixed_code)

    register_res = await inprocess_client.post("/auth/register", json={"email": email})
    assert register_res.status_code == 200

    verify_res = await inprocess_client.post(
        "/auth/verify",
        json={"email": email, "code": fixed_code},
    )
    assert verify_res.status_code == 200

    tokens = verify_res.json()["data"]
    access_token = tokens["access_token"]

    me_res = await inprocess_client.get(
        "/users/me",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    assert me_res.status_code == 200, me_res.text

    body = me_res.json()
    assert body["success"] is True

    data = body["data"]
    assert data["email"] == email.lower()
    assert data["is_verified"] is True
    assert data["id"]
    assert data["username"]
    assert data["created_at"]
    assert data["updated_at"]

    assert data["display_name"] is None
    assert data["bio"] is None
    assert data["avatar"] is None
    assert data["is_private"] is False
    assert data["default_discovery_enabled"] is True
    assert data["last_seen_at"] is None
    assert data["username_updated_at"] is None


@pytest.mark.asyncio
async def test_update_profile_data(inprocess_client, monkeypatch):
    email = f"user-{uuid.uuid4().hex[:8]}@test.com"
    fixed_code = "123456"

    monkeypatch.setattr(auth_service, "_generate_6_digit_code", lambda: fixed_code)

    register_res = await inprocess_client.post("/auth/register", json={"email": email})
    assert register_res.status_code == 200

    verify_res = await inprocess_client.post(
        "/auth/verify",
        json={"email": email, "code": fixed_code},
    )
    assert verify_res.status_code == 200

    access_token = verify_res.json()["data"]["access_token"]

    update_res = await inprocess_client.patch(
        "/users/me",
        headers={"Authorization": f"Bearer {access_token}"},
        json={
            "display_name": "Vachagan",
            "bio": "Բարև hello привет 👋",
            "is_private": True,
            "default_discovery_enabled": False,
        },
    )
    assert update_res.status_code == 200, update_res.text

    body = update_res.json()
    assert body["success"] is True

    data = body["data"]
    assert data["display_name"] == "Vachagan"
    assert data["bio"] == "Բարև hello привет 👋"
    assert data["is_private"] is True
    assert data["default_discovery_enabled"] is False

    me_res = await inprocess_client.get(
        "/users/me",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    assert me_res.status_code == 200, me_res.text

    me_data = me_res.json()["data"]
    assert me_data["display_name"] == "Vachagan"
    assert me_data["bio"] == "Բարև hello привет 👋"
    assert me_data["is_private"] is True
    assert me_data["default_discovery_enabled"] is False


@pytest.mark.asyncio
async def test_update_username(inprocess_client, monkeypatch):
    email = f"user-{uuid.uuid4().hex[:8]}@test.com"
    fixed_code = "123456"

    monkeypatch.setattr(auth_service, "_generate_6_digit_code", lambda: fixed_code)

    register_res = await inprocess_client.post("/auth/register", json={"email": email})
    assert register_res.status_code == 200

    verify_res = await inprocess_client.post(
        "/auth/verify",
        json={"email": email, "code": fixed_code},
    )
    assert verify_res.status_code == 200

    access_token = verify_res.json()["data"]["access_token"]

    update_res = await inprocess_client.patch(
        "/users/me/username",
        headers={"Authorization": f"Bearer {access_token}"},
        json={"username": "swift-fox-27"},
    )
    assert update_res.status_code == 200, update_res.text

    body = update_res.json()
    assert body["success"] is True

    data = body["data"]
    assert data["username"] == "swift-fox-27"
    assert data["username_updated_at"] is not None

    me_res = await inprocess_client.get(
        "/users/me",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    assert me_res.status_code == 200, me_res.text

    me_data = me_res.json()["data"]
    assert me_data["username"] == "swift-fox-27"


@pytest.mark.asyncio
async def test_update_username_rejects_duplicate(inprocess_client, monkeypatch):
    fixed_code = "123456"
    monkeypatch.setattr(auth_service, "_generate_6_digit_code", lambda: fixed_code)

    email_a = f"user-a-{uuid.uuid4().hex[:8]}@test.com"
    email_b = f"user-b-{uuid.uuid4().hex[:8]}@test.com"

    reg_a = await inprocess_client.post("/auth/register", json={"email": email_a})
    assert reg_a.status_code == 200
    ver_a = await inprocess_client.post(
        "/auth/verify",
        json={"email": email_a, "code": fixed_code},
    )
    assert ver_a.status_code == 200
    token_a = ver_a.json()["data"]["access_token"]

    reg_b = await inprocess_client.post("/auth/register", json={"email": email_b})
    assert reg_b.status_code == 200
    ver_b = await inprocess_client.post(
        "/auth/verify",
        json={"email": email_b, "code": fixed_code},
    )
    assert ver_b.status_code == 200
    token_b = ver_b.json()["data"]["access_token"]

    set_a = await inprocess_client.patch(
        "/users/me/username",
        headers={"Authorization": f"Bearer {token_a}"},
        json={"username": "blue-orbit-84"},
    )
    assert set_a.status_code == 200, set_a.text

    set_b = await inprocess_client.patch(
        "/users/me/username",
        headers={"Authorization": f"Bearer {token_b}"},
        json={"username": "blue-orbit-84"},
    )
    assert set_b.status_code == 409, set_b.text

    body = set_b.json()
    assert body["error"]["code"] == "USERNAME_TAKEN"


@pytest.mark.asyncio
async def test_get_me_requires_auth(inprocess_client):
    res = await inprocess_client.get("/users/me")
    assert res.status_code in (401, 403), res.text


@pytest.mark.asyncio
async def test_get_selected_user_profile_allows_accepted_ping(inprocess_client):
    viewer, viewer_tokens = await _create_verified_user_and_tokens(f"viewer-{uuid.uuid4().hex[:8]}@test.com")
    target, target_tokens = await _create_verified_user_and_tokens(f"target-{uuid.uuid4().hex[:8]}@test.com")

    await _grant_chat_permission(str(viewer["_id"]), str(target["_id"]))

    update_res = await inprocess_client.patch(
        "/users/me",
        headers={"Authorization": f"Bearer {target_tokens['access_token']}"},
        json={
            "display_name": "Target User",
            "bio": "Visible through accepted ping",
        },
    )
    assert update_res.status_code == 200, update_res.text

    res = await inprocess_client.get(
        f"/users/{target['_id']}",
        headers={"Authorization": f"Bearer {viewer_tokens['access_token']}"},
    )
    assert res.status_code == 200, res.text

    body = res.json()
    assert body["success"] is True

    data = body["data"]
    assert data == {
        "id": str(target["_id"]),
        "username": target["username"],
        "display_name": "Target User",
        "bio": "Visible through accepted ping",
        "avatar": None,
        "is_online": False,
    }
    assert "email" not in data
    assert "is_private" not in data
    assert "default_discovery_enabled" not in data


@pytest.mark.asyncio
async def test_get_selected_user_profile_forbids_without_accepted_ping(inprocess_client):
    viewer, viewer_tokens = await _create_verified_user_and_tokens(f"viewer-no-ping-{uuid.uuid4().hex[:8]}@test.com")
    target, _ = await _create_verified_user_and_tokens(f"target-no-ping-{uuid.uuid4().hex[:8]}@test.com")

    res = await inprocess_client.get(
        f"/users/{target['_id']}",
        headers={"Authorization": f"Bearer {viewer_tokens['access_token']}"},
    )
    assert res.status_code == 403, res.text

    body = res.json()
    assert body["error"]["code"] == "PROFILE_ACCESS_FORBIDDEN"


@pytest.mark.asyncio
async def test_get_selected_user_profile_allows_self_without_ping(inprocess_client):
    user, tokens = await _create_verified_user_and_tokens(f"self-{uuid.uuid4().hex[:8]}@test.com")

    res = await inprocess_client.get(
        f"/users/{user['_id']}",
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
    )
    assert res.status_code == 200, res.text

    data = res.json()["data"]
    assert data["id"] == str(user["_id"])
    assert data["username"] == user["username"]
    assert data["is_online"] is False
    assert "email" not in data


@pytest.mark.asyncio
async def test_get_selected_user_profile_returns_not_found_for_missing_user(inprocess_client):
    viewer, viewer_tokens = await _create_verified_user_and_tokens(f"viewer-missing-{uuid.uuid4().hex[:8]}@test.com")

    res = await inprocess_client.get(
        f"/users/{uuid.uuid4().hex[:24]}",
        headers={"Authorization": f"Bearer {viewer_tokens['access_token']}"},
    )
    assert res.status_code == 404, res.text

    body = res.json()
    assert body["error"]["code"] == "USER_NOT_FOUND"


@pytest.mark.asyncio
async def test_update_profile_requires_auth(inprocess_client):
    res = await inprocess_client.patch(
        "/users/me",
        json={"display_name": "Test"},
    )
    assert res.status_code in (401, 403), res.text


@pytest.mark.asyncio
async def test_update_username_rejects_invalid_value(inprocess_client, monkeypatch):
    email = f"user-{uuid.uuid4().hex[:8]}@test.com"
    fixed_code = "123456"

    monkeypatch.setattr(auth_service, "_generate_6_digit_code", lambda: fixed_code)

    register_res = await inprocess_client.post("/auth/register", json={"email": email})
    assert register_res.status_code == 200

    verify_res = await inprocess_client.post(
        "/auth/verify",
        json={"email": email, "code": fixed_code},
    )
    assert verify_res.status_code == 200

    access_token = verify_res.json()["data"]["access_token"]

    update_res = await inprocess_client.patch(
        "/users/me/username",
        headers={"Authorization": f"Bearer {access_token}"},
        json={"username": "admin"},
    )
    assert update_res.status_code == 400, update_res.text

    body = update_res.json()
    assert body["error"]["code"] == "INVALID_USERNAME"
