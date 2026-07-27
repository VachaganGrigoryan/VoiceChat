from __future__ import annotations

from datetime import UTC, datetime
from time import perf_counter

import pytest
from bson import ObjectId

from app.db.collections import COL_CHANNELS, COL_RELATIONSHIPS
from app.db.models import UserDocument
from app.db.mongo import get_db
from app.modules.auth.repository import UsersRepository
from app.modules.channels.repository import ChannelsRepository
from app.modules.channels.service import ChannelService
from app.tests.integration.test_realtime_socket import (
    _create_verified_user_and_tokens,
)


def _auth(access_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {access_token}"}


@pytest.mark.asyncio
async def test_home_profile_and_channel_feeds_are_root_only_and_paginated(
    inprocess_client,
):
    viewer, viewer_tokens = await _create_verified_user_and_tokens(
        "home-feed-viewer@test.com"
    )
    profile_owner, profile_tokens = await _create_verified_user_and_tokens(
        "home-feed-profile@test.com"
    )
    _channel_owner, channel_tokens = await _create_verified_user_and_tokens(
        "home-feed-channel@test.com"
    )

    profile_user = await UserDocument.get(profile_owner["_id"])
    assert profile_user is not None
    profile_channel = await ChannelService(
        repo=ChannelsRepository()
    ).ensure_profile_channel(
        user_id=profile_user.str_id,
        username=profile_user.username,
    )
    profile_user = await UsersRepository().update_main_channel(
        user_id=profile_user.str_id,
        channel_id=profile_channel.str_id,
    )

    profile_post = await inprocess_client.post(
        "/users/me/posts",
        json={"text": "Profile root"},
        headers=_auth(profile_tokens["access_token"]),
    )
    assert profile_post.status_code == 201, profile_post.text
    profile_post_id = profile_post.json()["data"]["id"]
    profile_comment = await inprocess_client.post(
        f"/channels/{profile_user.main_channel_id}/messages",
        json={
            "text": "Profile comment",
            "reply_mode": "thread",
            "reply_to_message_id": profile_post_id,
        },
        headers=_auth(profile_tokens["access_token"]),
    )
    assert profile_comment.status_code == 201, profile_comment.text

    public_channel = await inprocess_client.post(
        "/channels",
        json={
            "name": "Public feed",
            "slug": "public-feed",
            "visibility": "public",
            "posting_policy": "owner",
        },
        headers=_auth(channel_tokens["access_token"]),
    )
    assert public_channel.status_code == 201, public_channel.text
    public_channel_id = public_channel.json()["data"]["id"]
    channel_post = await inprocess_client.post(
        f"/channels/{public_channel_id}/messages",
        json={"text": "Channel root"},
        headers=_auth(channel_tokens["access_token"]),
    )
    assert channel_post.status_code == 201, channel_post.text
    channel_comment = await inprocess_client.post(
        f"/channels/{public_channel_id}/messages",
        json={
            "text": "Channel comment",
            "reply_mode": "thread",
            "reply_to_message_id": channel_post.json()["data"]["id"],
        },
        headers=_auth(channel_tokens["access_token"]),
    )
    assert channel_comment.status_code == 201, channel_comment.text

    members_channel = await inprocess_client.post(
        "/channels",
        json={
            "name": "Members feed",
            "slug": "members-feed",
            "visibility": "members",
            "join_policy": "open",
            "posting_policy": "owner",
        },
        headers=_auth(channel_tokens["access_token"]),
    )
    assert members_channel.status_code == 201, members_channel.text
    members_channel_id = members_channel.json()["data"]["id"]
    hidden_post = await inprocess_client.post(
        f"/channels/{members_channel_id}/messages",
        json={"text": "Hidden root"},
        headers=_auth(channel_tokens["access_token"]),
    )
    assert hidden_post.status_code == 201, hidden_post.text

    for path in (
        f"/users/{profile_owner['_id']}/follow",
        f"/channels/{public_channel_id}/follow",
        f"/channels/{members_channel_id}/follow",
    ):
        follow = await inprocess_client.post(
            path,
            headers=_auth(viewer_tokens["access_token"]),
        )
        assert follow.status_code == 201, follow.text
        assert follow.json()["data"]["status"] == "active"

    first_page = await inprocess_client.get(
        "/feeds",
        params={"limit": 1},
        headers=_auth(viewer_tokens["access_token"]),
    )
    assert first_page.status_code == 200, first_page.text
    assert [item["text"] for item in first_page.json()["data"]] == ["Channel root"]
    next_cursor = first_page.json()["meta"]["next_cursor"]
    assert next_cursor is not None

    second_page = await inprocess_client.get(
        "/feeds",
        params={"limit": 1, "cursor": next_cursor},
        headers=_auth(viewer_tokens["access_token"]),
    )
    assert second_page.status_code == 200, second_page.text
    assert [item["text"] for item in second_page.json()["data"]] == ["Profile root"]
    assert second_page.json()["meta"]["next_cursor"] is None

    profile_feed = await inprocess_client.get(
        f"/feeds/users/{profile_user.username}",
        headers=_auth(viewer_tokens["access_token"]),
    )
    assert profile_feed.status_code == 200, profile_feed.text
    assert [item["text"] for item in profile_feed.json()["data"]] == ["Profile root"]

    channel_feed = await inprocess_client.get(
        f"/feeds/channels/{public_channel_id}",
        headers=_auth(viewer_tokens["access_token"]),
    )
    assert channel_feed.status_code == 200, channel_feed.text
    assert [item["text"] for item in channel_feed.json()["data"]] == ["Channel root"]

    home_texts = {
        item["text"]
        for page in (first_page, second_page)
        for item in page.json()["data"]
    }
    assert home_texts == {"Profile root", "Channel root"}
    assert "Profile comment" not in home_texts
    assert "Channel comment" not in home_texts
    assert "Hidden root" not in home_texts


@pytest.mark.asyncio
async def test_home_feed_handles_one_thousand_followed_channels(inprocess_client):
    viewer, viewer_tokens = await _create_verified_user_and_tokens(
        "home-feed-load-viewer@test.com"
    )
    owner, _owner_tokens = await _create_verified_user_and_tokens(
        "home-feed-load-owner@test.com"
    )
    viewer_id = str(viewer["_id"])
    owner_id = str(owner["_id"])
    now = datetime.now(UTC)
    channel_ids = [ObjectId() for _ in range(1000)]
    db = get_db()

    await db[COL_CHANNELS].insert_many(
        [
            {
                "_id": channel_id,
                "owner": {"type": "user", "id": owner_id},
                "kind": "text",
                "slug": f"load-{index}",
                "name": f"Load {index}",
                "visibility": "public",
                "created_by": owner_id,
                "created_at": now,
                "updated_at": now,
            }
            for index, channel_id in enumerate(channel_ids)
        ]
    )
    await db[COL_RELATIONSHIPS].insert_many(
        [
            {
                "_id": ObjectId(),
                "kind": "follow",
                "user_id": viewer_id,
                "target_type": "channel",
                "target_id": str(channel_id),
                "status": "active",
                "initiation": "direct",
                "initiated_by": viewer_id,
                "requested_at": now,
                "activated_at": now,
                "created_at": now,
                "updated_at": now,
            }
            for channel_id in channel_ids
        ]
    )

    started_at = perf_counter()
    response = await inprocess_client.get(
        "/feeds",
        headers=_auth(viewer_tokens["access_token"]),
    )
    elapsed = perf_counter() - started_at

    assert response.status_code == 200, response.text
    assert response.json()["data"] == []
    assert elapsed < 5.0, f"1,000-follow home feed took {elapsed:.3f}s"
    assert "feed_entries" not in await db.list_collection_names()
