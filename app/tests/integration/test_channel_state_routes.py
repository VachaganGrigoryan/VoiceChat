from __future__ import annotations

import pytest

from app.tests.integration.test_realtime_socket import (
    _create_verified_user_and_tokens,
)


def _auth(access_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {access_token}"}


async def _make_channel(client, tokens, slug: str) -> str:
    created = await client.post(
        "/channels",
        json={
            "name": slug,
            "kind": "text",
            "visibility": "public",
            "join_policy": "open",
            "posting_policy": "everyone",
            "comment_policy": "everyone",
            "slug": slug,
        },
        headers=_auth(tokens["access_token"]),
    )
    assert created.status_code == 201, created.text
    return created.json()["data"]["id"]


@pytest.mark.asyncio
async def test_channel_inbox_reports_state_and_unread(inprocess_client):
    """The inbox row must carry enough per-user state to render like a DM row."""
    _owner, owner_tokens = await _create_verified_user_and_tokens("state-owner@test.com")
    _member, member_tokens = await _create_verified_user_and_tokens(
        "state-member@test.com"
    )
    channel_id = await _make_channel(inprocess_client, owner_tokens, "state-channel")

    joined = await inprocess_client.post(
        f"/channels/{channel_id}/join", headers=_auth(member_tokens["access_token"])
    )
    assert joined.status_code == 201, joined.text

    inbox = await inprocess_client.get(
        "/channels/me", headers=_auth(member_tokens["access_token"])
    )
    assert inbox.status_code == 200, inbox.text
    rows = inbox.json()["data"]
    assert len(rows) == 1
    assert rows[0]["channel"]["id"] == channel_id
    assert rows[0]["joined"] is True
    assert rows[0]["state"]["notification_level"] == "all"
    assert rows[0]["state"]["pinned"] is False

    # Owner posts; the member has never read, so everything is unread.
    for index in range(3):
        posted = await inprocess_client.post(
            f"/messages/channel/{channel_id}/text",
            json={"text": f"message {index}"},
            headers=_auth(owner_tokens["access_token"]),
        )
        assert posted.status_code == 201, posted.text

    inbox = await inprocess_client.get(
        "/channels/me", headers=_auth(member_tokens["access_token"])
    )
    assert inbox.json()["data"][0]["unread_count"] == 3

    read = await inprocess_client.post(
        f"/channels/{channel_id}/read", headers=_auth(member_tokens["access_token"])
    )
    assert read.status_code == 200, read.text

    inbox = await inprocess_client.get(
        "/channels/me", headers=_auth(member_tokens["access_token"])
    )
    assert inbox.json()["data"][0]["unread_count"] == 0


@pytest.mark.asyncio
async def test_channel_pin_and_mute_round_trip(inprocess_client):
    _owner, owner_tokens = await _create_verified_user_and_tokens("pin-owner@test.com")
    _member, member_tokens = await _create_verified_user_and_tokens(
        "pin-member@test.com"
    )
    channel_id = await _make_channel(inprocess_client, owner_tokens, "pin-channel")
    await inprocess_client.post(
        f"/channels/{channel_id}/join", headers=_auth(member_tokens["access_token"])
    )

    pinned = await inprocess_client.patch(
        f"/channels/{channel_id}/inbox",
        json={"pinned": True},
        headers=_auth(member_tokens["access_token"]),
    )
    assert pinned.status_code == 200, pinned.text
    assert pinned.json()["data"]["pinned"] is True

    muted = await inprocess_client.patch(
        f"/channels/{channel_id}/notifications",
        json={"notification_level": "mentions"},
        headers=_auth(member_tokens["access_token"]),
    )
    assert muted.status_code == 200, muted.text
    assert muted.json()["data"]["notification_level"] == "mentions"

    inbox = await inprocess_client.get(
        "/channels/me", headers=_auth(member_tokens["access_token"])
    )
    state = inbox.json()["data"][0]["state"]
    assert state["pinned"] is True
    assert state["notification_level"] == "mentions"


@pytest.mark.asyncio
async def test_channel_members_and_leave(inprocess_client):
    _owner, owner_tokens = await _create_verified_user_and_tokens(
        "leave-owner@test.com"
    )
    _member, member_tokens = await _create_verified_user_and_tokens(
        "leave-member@test.com"
    )
    channel_id = await _make_channel(inprocess_client, owner_tokens, "leave-channel")
    await inprocess_client.post(
        f"/channels/{channel_id}/join", headers=_auth(member_tokens["access_token"])
    )

    members = await inprocess_client.get(
        f"/channels/{channel_id}/members", headers=_auth(owner_tokens["access_token"])
    )
    assert members.status_code == 200, members.text
    assert len(members.json()["data"]) >= 1

    left = await inprocess_client.post(
        f"/channels/{channel_id}/leave", headers=_auth(member_tokens["access_token"])
    )
    assert left.status_code == 200, left.text

    inbox = await inprocess_client.get(
        "/channels/me", headers=_auth(member_tokens["access_token"])
    )
    assert inbox.json()["data"] == []

    # Leaving twice is a conflict, not a silent success.
    again = await inprocess_client.post(
        f"/channels/{channel_id}/leave", headers=_auth(member_tokens["access_token"])
    )
    assert again.status_code == 409, again.text


@pytest.mark.asyncio
async def test_channel_state_requires_membership(inprocess_client):
    _owner, owner_tokens = await _create_verified_user_and_tokens(
        "stranger-owner@test.com"
    )
    _stranger, stranger_tokens = await _create_verified_user_and_tokens(
        "stranger@test.com"
    )
    channel_id = await _make_channel(inprocess_client, owner_tokens, "stranger-channel")

    denied = await inprocess_client.patch(
        f"/channels/{channel_id}/inbox",
        json={"pinned": True},
        headers=_auth(stranger_tokens["access_token"]),
    )
    assert denied.status_code == 409, denied.text
