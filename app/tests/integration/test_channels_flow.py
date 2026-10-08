from __future__ import annotations

import pytest

from app.db.models import MessageReceiptDocument, RelationshipDocument, UserDocument
from app.modules.auth.repository import UsersRepository
from app.modules.channels.repository import ChannelsRepository
from app.modules.channels.service import ChannelService
from app.modules.messages.dependencies import get_messages_service
from app.modules.messages.schemas import MediaAttachmentInput, SendRichContentRequest
from app.modules.relationships.service import RelationshipService
from app.tests.integration.test_realtime_socket import (
    _create_verified_user_and_tokens,
)


def _auth(access_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {access_token}"}


@pytest.mark.asyncio
async def test_user_creation_provisions_profile_channel(app_lifecycle):
    user = await UsersRepository().create_user("signup-profile@test.com")

    assert user.main_channel_id is not None
    channel = await ChannelsRepository().get_by_id(user.main_channel_id)
    assert channel is not None
    assert channel.kind == "profile"
    assert channel.owner.type == "user"
    assert str(channel.owner.id) == user.str_id
    assert channel.visibility == "public"
    assert channel.posting_policy == "owner"
    assert channel.comment_policy == "everyone"


@pytest.mark.asyncio
async def test_channel_policy_posts_comments_and_counters(inprocess_client):
    owner, owner_tokens = await _create_verified_user_and_tokens("chan-owner@test.com")
    _stranger, stranger_tokens = await _create_verified_user_and_tokens(
        "chan-stranger@test.com"
    )

    created = await inprocess_client.post(
        "/channels",
        json={
            "name": "Announcements",
            "description": "Company news",
            "kind": "announcement",
            "visibility": "public",
            "posting_policy": "owner",
            "comment_policy": "everyone",
            "slug": "announcements",
        },
        headers=_auth(owner_tokens["access_token"]),
    )
    assert created.status_code == 201, created.text
    channel_id = created.json()["data"]["id"]

    public_read = await inprocess_client.get(
        f"/channels/{channel_id}",
        headers=_auth(stranger_tokens["access_token"]),
    )
    assert public_read.status_code == 200, public_read.text

    follow = await RelationshipService().request(
        kind="follow",
        user_id=str(_stranger["_id"]),
        target_type="channel",
        target_id=channel_id,
        status="active",
    )

    denied = await inprocess_client.post(
        f"/messages/channel/{channel_id}/text",
        json={"text": "not allowed"},
        headers=_auth(stranger_tokens["access_token"]),
    )
    assert denied.status_code == 403, denied.text

    post = await inprocess_client.post(
        f"/messages/channel/{channel_id}/text",
        json={"text": "Release notes"},
        headers=_auth(owner_tokens["access_token"]),
    )
    assert post.status_code == 201, post.text
    post_data = post.json()["data"]
    assert post_data["container_type"] == "channel"
    assert post_data["container_id"] == channel_id
    assert post_data["thread_root_id"] is None
    assert (
        await MessageReceiptDocument.find(
            {"message_id": post_data["id"]}
        ).count()
        == 0
    )

    marked_read = await inprocess_client.post(
        f"/messages/{post_data['id']}/read",
        headers=_auth(stranger_tokens["access_token"]),
    )
    assert marked_read.status_code == 200, marked_read.text
    refreshed_follow = await RelationshipDocument.get(follow.id)
    assert refreshed_follow is not None
    assert refreshed_follow.state.last_read_message_id == post_data["id"]

    comment = await inprocess_client.post(
        f"/messages/channel/{channel_id}/text",
        json={
            "text": "Looks good",
            "reply_mode": "thread",
            "reply_to_message_id": post_data["id"],
        },
        headers=_auth(stranger_tokens["access_token"]),
    )
    assert comment.status_code == 201, comment.text
    assert comment.json()["data"]["thread_root_id"] == post_data["id"]

    refreshed = await inprocess_client.get(
        f"/channels/{channel_id}",
        headers=_auth(owner_tokens["access_token"]),
    )
    assert refreshed.json()["data"]["message_count"] == 2
    assert refreshed.json()["data"]["last_message_id"] == comment.json()["data"]["id"]


@pytest.mark.asyncio
async def test_private_channel_requires_active_membership(inprocess_client):
    owner, owner_tokens = await _create_verified_user_and_tokens("private-owner@test.com")
    stranger, stranger_tokens = await _create_verified_user_and_tokens(
        "private-stranger@test.com"
    )
    created = await inprocess_client.post(
        "/channels",
        json={
            "name": "Private",
            "slug": "private",
            "visibility": "private",
        },
        headers=_auth(owner_tokens["access_token"]),
    )
    channel_id = created.json()["data"]["id"]

    denied = await inprocess_client.get(
        f"/messages/channel/{channel_id}",
        headers=_auth(stranger_tokens["access_token"]),
    )
    assert denied.status_code == 403, denied.text

    from app.modules.relationships.service import RelationshipService

    await RelationshipService().request(
        kind="membership",
        user_id=str(stranger["_id"]),
        target_type="channel",
        target_id=channel_id,
        status="active",
    )
    allowed = await inprocess_client.get(
        f"/messages/channel/{channel_id}",
        headers=_auth(stranger_tokens["access_token"]),
    )
    assert allowed.status_code == 200, allowed.text


@pytest.mark.asyncio
async def test_profile_alias_renders_posts_media_and_comments(inprocess_client):
    owner, owner_tokens = await _create_verified_user_and_tokens("profile-feed@test.com")
    user = await UserDocument.get(owner["_id"])
    assert user is not None
    channel = await ChannelService(repo=ChannelsRepository()).ensure_profile_channel(
        user_id=user.str_id,
        username=user.username,
    )
    await UsersRepository().update_main_channel(
        user_id=user.str_id,
        channel_id=channel.str_id,
    )

    post = await inprocess_client.post(
        "/users/me/posts",
        json={"text": "Profile post"},
        headers=_auth(owner_tokens["access_token"]),
    )
    assert post.status_code == 201, post.text

    await get_messages_service().send_rich_content(
        container_type="channel",
        container_id=channel.str_id,
        sender_id=user.str_id,
        body=SendRichContentRequest(
            type="voice",
            attachments=[
                MediaAttachmentInput(
                    kind="voice",
                    storage="local",
                    key="tests/profile-note.webm",
                    mime="audio/webm",
                    size_bytes=128,
                    duration_ms=1000,
                )
            ],
        ),
    )
    comment = await inprocess_client.post(
        f"/messages/channel/{channel.str_id}/text",
        json={
            "text": "Profile comment",
            "reply_mode": "thread",
            "reply_to_message_id": post.json()["data"]["id"],
        },
        headers=_auth(owner_tokens["access_token"]),
    )
    assert comment.status_code == 201, comment.text

    feed = await inprocess_client.get(
        f"/feeds/users/{user.username}",
        headers=_auth(owner_tokens["access_token"]),
    )
    assert feed.status_code == 200, feed.text
    items = feed.json()["data"]
    assert {item["type"] for item in items} == {"text", "voice"}
    assert any(item["attachments"] for item in items if item["type"] == "voice")

    comments = await inprocess_client.get(
        f"/feeds/channels/{channel.str_id}/posts/{post.json()['data']['id']}/comments",
        headers=_auth(owner_tokens["access_token"]),
    )
    assert comments.status_code == 200, comments.text
    assert any(item["text"] == "Profile comment" for item in comments.json()["data"])


@pytest.mark.asyncio
async def test_private_profile_requires_approved_follow(inprocess_client):
    owner, owner_tokens = await _create_verified_user_and_tokens("private-profile@test.com")
    stranger, stranger_tokens = await _create_verified_user_and_tokens(
        "private-profile-follower@test.com"
    )
    user = await UserDocument.get(owner["_id"])
    assert user is not None
    channel = await ChannelService(repo=ChannelsRepository()).ensure_profile_channel(
        user_id=user.str_id,
        username=user.username,
    )
    await UsersRepository().update_main_channel(
        user_id=user.str_id,
        channel_id=channel.str_id,
    )

    privacy = await inprocess_client.patch(
        "/users/me",
        json={"is_private": True},
        headers=_auth(owner_tokens["access_token"]),
    )
    assert privacy.status_code == 200, privacy.text
    private_channel = await ChannelsRepository().get_by_id(channel.str_id)
    assert private_channel is not None
    assert private_channel.visibility == "members"

    follow = await inprocess_client.post(
        f"/users/{owner['_id']}/follow",
        headers=_auth(stranger_tokens["access_token"]),
    )
    assert follow.status_code == 201, follow.text
    assert follow.json()["data"]["status"] == "pending"

    denied = await inprocess_client.get(
        f"/messages/channel/{channel.str_id}",
        headers=_auth(stranger_tokens["access_token"]),
    )
    assert denied.status_code == 403, denied.text

    accepted = await inprocess_client.post(
        f"/follows/{follow.json()['data']['id']}/accept",
        headers=_auth(owner_tokens["access_token"]),
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["data"]["status"] == "active"

    allowed = await inprocess_client.get(
        f"/messages/channel/{channel.str_id}",
        headers=_auth(stranger_tokens["access_token"]),
    )
    assert allowed.status_code == 200, allowed.text
    refreshed = await ChannelsRepository().get_by_id(channel.str_id)
    assert refreshed is not None
    assert refreshed.follower_count == 1
    assert str(stranger["_id"]) == accepted.json()["data"]["user_id"]


@pytest.mark.asyncio
async def test_conversation_api_no_longer_serves_channels(inprocess_client):
    _owner, owner_tokens = await _create_verified_user_and_tokens("legacy-channel@test.com")

    response = await inprocess_client.post(
        "/conversations/channels",
        json={"title": "Legacy", "participant_ids": []},
        headers=_auth(owner_tokens["access_token"]),
    )

    assert response.status_code == 405


@pytest.mark.asyncio
async def test_group_creation_still_works(inprocess_client):
    """Guard against the narrowing over-reaching into the surviving conversation types."""
    owner, owner_tokens = await _create_verified_user_and_tokens("chan-grp-o@test.com")
    member, _member_tokens = await _create_verified_user_and_tokens("chan-grp-m@test.com")
    from app.tests.integration.test_realtime_socket import _grant_chat_permission

    await _grant_chat_permission(str(owner["_id"]), str(member["_id"]))

    created = await inprocess_client.post(
        "/conversations/groups",
        json={"title": "Broadcast", "participant_ids": [str(member["_id"])]},
        headers=_auth(owner_tokens["access_token"]),
    )
    assert created.status_code == 201, created.text
    assert created.json()["data"]["type"] == "group"
