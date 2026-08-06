from __future__ import annotations

import pytest

from app.tests.integration.test_realtime_socket import _create_verified_user_and_tokens

# The fields the retired `UserChannelView` renamed. A client that sees any of
# these is looking at a second shape for an entity that must have exactly one.
RETIRED_FIELDS = {"title", "member_count", "last_message_at", "read_policy"}

SUMMARY_FIELDS = {
    "id",
    "owner",
    "space_id",
    "kind",
    "slug",
    "name",
    "description",
    "avatar",
    "banner",
    "visibility",
    "tags",
    "message_count",
    "follower_count",
    "last_activity_at",
}


def _auth(access_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {access_token}"}


async def _user_with_channel(client, email: str, slug: str):
    """Create a user and one owned channel.

    The profile channel is created by the signup path, which this test helper
    bypasses, so channels are made explicitly rather than assumed.
    """
    user, tokens = await _create_verified_user_and_tokens(email)
    created = await client.post(
        "/channels",
        json={"name": slug.title(), "slug": slug},
        headers=_auth(tokens["access_token"]),
    )
    assert created.status_code == 201, created.text
    return user, tokens, created.json()["data"]["id"]


@pytest.mark.asyncio
async def test_user_channel_listing_uses_the_canonical_shape(inprocess_client):
    user, tokens, _ = await _user_with_channel(
        inprocess_client, "sum-u1@test.com", "sum-first"
    )

    resp = await inprocess_client.get(
        f"/users/{user['_id']}/channels", headers=_auth(tokens["access_token"])
    )
    assert resp.status_code == 200, resp.text
    channels = resp.json()["data"]
    assert channels

    for channel in channels:
        assert SUMMARY_FIELDS <= set(channel), SUMMARY_FIELDS - set(channel)
        assert not RETIRED_FIELDS & set(channel), RETIRED_FIELDS & set(channel)


@pytest.mark.asyncio
async def test_detail_extends_the_summary_without_renaming(inprocess_client):
    user, tokens = await _create_verified_user_and_tokens("sum-u2@test.com")
    created = await inprocess_client.post(
        "/channels",
        json={"name": "Notes", "slug": "sum-notes"},
        headers=_auth(tokens["access_token"]),
    )
    assert created.status_code == 201, created.text
    channel_id = created.json()["data"]["id"]

    detail = await inprocess_client.get(
        f"/channels/{channel_id}", headers=_auth(tokens["access_token"])
    )
    assert detail.status_code == 200, detail.text
    body = detail.json()["data"]

    assert SUMMARY_FIELDS <= set(body)
    assert not RETIRED_FIELDS & set(body)
    # The detail view adds policy and audit fields; it renames nothing.
    assert {"join_policy", "posting_policy", "comment_policy", "created_at"} <= set(body)


@pytest.mark.asyncio
async def test_visibility_is_the_only_readability_vocabulary(inprocess_client):
    """`read_policy` is gone; a channel's audience is `visibility` alone."""
    user, tokens = await _create_verified_user_and_tokens("sum-u3@test.com")
    created = await inprocess_client.post(
        "/channels",
        json={"name": "Private", "slug": "sum-private", "visibility": "members"},
        headers=_auth(tokens["access_token"]),
    )
    channel_id = created.json()["data"]["id"]

    detail = await inprocess_client.get(
        f"/channels/{channel_id}", headers=_auth(tokens["access_token"])
    )
    body = detail.json()["data"]
    assert body["visibility"] == "members"
    assert "read_policy" not in body

    listing = await inprocess_client.get(
        f"/users/{user['_id']}/channels", headers=_auth(tokens["access_token"])
    )
    for channel in listing.json()["data"]:
        assert "read_policy" not in channel


@pytest.mark.asyncio
async def test_main_channel_is_flagged_and_sorted_first(inprocess_client):
    user, tokens, first_id = await _user_with_channel(
        inprocess_client, "sum-u4@test.com", "sum-fourth"
    )
    second = await inprocess_client.post(
        "/channels",
        json={"name": "Second", "slug": "sum-second"},
        headers=_auth(tokens["access_token"]),
    )
    assert second.status_code == 201, second.text

    pinned = await inprocess_client.patch(
        "/users/me/main-channel",
        json={"channel_id": first_id},
        headers=_auth(tokens["access_token"]),
    )
    assert pinned.status_code == 200, pinned.text

    resp = await inprocess_client.get(
        f"/users/{user['_id']}/channels", headers=_auth(tokens["access_token"])
    )
    channels = resp.json()["data"]
    assert len(channels) == 2
    assert channels[0]["id"] == first_id
    assert channels[0]["is_main"] is True
    assert all(channel["is_main"] is False for channel in channels[1:])
