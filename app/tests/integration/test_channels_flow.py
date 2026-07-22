from __future__ import annotations

import pytest

from app.tests.integration.test_realtime_socket import (
    _create_verified_user_and_tokens,
    _grant_chat_permission,
)


def _auth(access_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {access_token}"}


@pytest.mark.asyncio
async def test_create_public_channel_with_slug(inprocess_client):
    owner, owner_tokens = await _create_verified_user_and_tokens("chan-owner@test.com")

    resp = await inprocess_client.post(
        "/conversations/channels",
        json={
            "title": "Announcements",
            "description": "Company news",
            "visibility": "public",
            "posting_policy": "admins",
            "slug": "announcements",
        },
        headers=_auth(owner_tokens["access_token"]),
    )

    assert resp.status_code == 201, resp.text
    data = resp.json()["data"]
    assert data["type"] == "channel"
    assert data["visibility"] == "public"
    assert data["posting_policy"] == "admins"
    assert data["slug"] == "announcements"
    assert data["description"] == "Company news"
    assert data["member_count"] == 1


@pytest.mark.asyncio
async def test_public_slug_uniqueness_conflict(inprocess_client):
    owner, owner_tokens = await _create_verified_user_and_tokens("chan-dup1@test.com")
    other, other_tokens = await _create_verified_user_and_tokens("chan-dup2@test.com")

    first = await inprocess_client.post(
        "/conversations/channels",
        json={"title": "News", "visibility": "public", "slug": "news"},
        headers=_auth(owner_tokens["access_token"]),
    )
    assert first.status_code == 201, first.text

    conflict = await inprocess_client.post(
        "/conversations/channels",
        json={"title": "News 2", "visibility": "public", "slug": "news"},
        headers=_auth(other_tokens["access_token"]),
    )
    assert conflict.status_code == 409, conflict.text
    assert conflict.json()["error"]["code"] == "CONVERSATION_SLUG_TAKEN"


@pytest.mark.asyncio
async def test_public_channel_discoverable_by_slug(inprocess_client):
    owner, owner_tokens = await _create_verified_user_and_tokens("chan-disc1@test.com")
    seeker, seeker_tokens = await _create_verified_user_and_tokens("chan-disc2@test.com")

    created = await inprocess_client.post(
        "/conversations/channels",
        json={"title": "Public Hub", "visibility": "public", "slug": "public-hub"},
        headers=_auth(owner_tokens["access_token"]),
    )
    assert created.status_code == 201, created.text
    channel_id = created.json()["data"]["id"]

    found = await inprocess_client.get(
        "/conversations/public/public-hub",
        headers=_auth(seeker_tokens["access_token"]),
    )
    assert found.status_code == 200, found.text
    assert found.json()["data"]["id"] == channel_id

    missing = await inprocess_client.get(
        "/conversations/public/does-not-exist",
        headers=_auth(seeker_tokens["access_token"]),
    )
    assert missing.status_code == 404, missing.text


@pytest.mark.asyncio
async def test_private_channel_not_discoverable_by_slug(inprocess_client):
    owner, owner_tokens = await _create_verified_user_and_tokens("chan-priv1@test.com")
    seeker, seeker_tokens = await _create_verified_user_and_tokens("chan-priv2@test.com")

    created = await inprocess_client.post(
        "/conversations/channels",
        json={"title": "Private Room", "visibility": "private"},
        headers=_auth(owner_tokens["access_token"]),
    )
    assert created.status_code == 201, created.text
    assert created.json()["data"]["slug"] is None


@pytest.mark.asyncio
async def test_posting_policy_admins_blocks_members(inprocess_client):
    owner, owner_tokens = await _create_verified_user_and_tokens("chan-post-o@test.com")
    member, member_tokens = await _create_verified_user_and_tokens("chan-post-m@test.com")
    await _grant_chat_permission(str(owner["_id"]), str(member["_id"]))

    created = await inprocess_client.post(
        "/conversations/channels",
        json={
            "title": "Broadcast",
            "posting_policy": "admins",
            "participant_ids": [str(member["_id"])],
        },
        headers=_auth(owner_tokens["access_token"]),
    )
    assert created.status_code == 201, created.text
    channel_id = created.json()["data"]["id"]

    # Member (read-only) is rejected.
    blocked = await inprocess_client.post(
        f"/conversations/{channel_id}/messages/text",
        json={"text": "can I post?"},
        headers=_auth(member_tokens["access_token"]),
    )
    assert blocked.status_code == 403, blocked.text
    assert blocked.json()["error"]["code"] == "CONVERSATION_POST_FORBIDDEN"

    # Owner (admin rights) may post.
    allowed = await inprocess_client.post(
        f"/conversations/{channel_id}/messages/text",
        json={"text": "official announcement"},
        headers=_auth(owner_tokens["access_token"]),
    )
    assert allowed.status_code == 201, allowed.text
