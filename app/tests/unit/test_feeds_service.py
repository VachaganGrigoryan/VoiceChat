from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core.errors import AppError
from app.modules.feeds.service import FeedService
from app.modules.messages.schemas import (
    MediaMeta,
    MessageContent,
    MessageDoc,
    MessagePlaintext,
    MessageReactionGroup,
)

OWNER = "507f1f77bcf86cd799439011"
VIEWER = "507f1f77bcf86cd799439012"


def make_channel(
    *,
    channel_id: str = "507f1f77bcf86cd7994390aa",
    visibility: str = "public",
):
    return SimpleNamespace(
        kind="text",
        owner=SimpleNamespace(type="user", id=OWNER),
        space_id=None,
        visibility=visibility,
        str_id=channel_id,
    )


def make_follow(*, target_type: str, target_id: str):
    return SimpleNamespace(
        target_type=target_type,
        target_id=target_id,
        status="active",
    )


def make_service(*, allowed: bool = True):
    authorization = AsyncMock()
    authorization.can.return_value = allowed
    return FeedService(
        channels_repo=AsyncMock(),
        messages_service=AsyncMock(),
        relationships_repo=AsyncMock(),
        users_repo=AsyncMock(),
        authorization=authorization,
    )


@pytest.mark.asyncio
async def test_authorization_allows_channel_view():
    svc = make_service()
    channel = make_channel()

    assert await svc._can_view_channel(viewer_id=VIEWER, channel=channel) is True
    svc.authorization.can.assert_awaited_once_with(
        VIEWER,
        "message.read",
        "channel",
        channel.str_id,
    )


@pytest.mark.asyncio
async def test_authorization_denies_channel_view():
    svc = make_service(allowed=False)
    channel = make_channel(visibility="private")
    assert await svc._can_view_channel(viewer_id=VIEWER, channel=channel) is False
    with pytest.raises(AppError):
        await svc.assert_can_view_channel(viewer_id=VIEWER, channel=channel)


@pytest.mark.asyncio
async def test_channel_feed_reads_channel_container():
    svc = make_service()
    channel = make_channel()
    svc.channels_repo.get_by_id.return_value = channel
    svc.messages.get_history.return_value = ([], None)

    await svc.list_channel_posts(
        viewer_id=VIEWER,
        channel_id=channel.str_id,
        limit=20,
        cursor=None,
    )

    svc.messages.get_history.assert_awaited_once_with(
        container_type="channel",
        container_id=channel.str_id,
        user_id=VIEWER,
        limit=20,
        cursor=None,
    )


@pytest.mark.asyncio
async def test_profile_feed_resolves_main_channel():
    svc = make_service()
    svc.users_repo.find_by_username.return_value = SimpleNamespace(
        main_channel_id="507f1f77bcf86cd7994390aa"
    )
    svc.channels_repo.get_by_id.return_value = make_channel()
    svc.messages.get_history.return_value = ([], None)

    await svc.list_profile_posts(
        viewer_id=VIEWER,
        username="alice",
        limit=20,
        cursor=None,
    )

    svc.messages.get_history.assert_awaited_once()


@pytest.mark.asyncio
async def test_home_resolves_followed_users_and_channels_with_visibility_filter():
    svc = make_service()
    followed_user_id = "507f1f77bcf86cd799439021"
    direct_channel_id = "507f1f77bcf86cd799439022"
    profile_channel_id = "507f1f77bcf86cd799439023"
    follows = [
        make_follow(target_type="user", target_id=followed_user_id),
        make_follow(target_type="channel", target_id=direct_channel_id),
    ]
    direct_channel = make_channel(channel_id=direct_channel_id)
    profile_channel = make_channel(channel_id=profile_channel_id)
    svc.relationships_repo.list_active_follows_for_user.return_value = follows
    svc.users_repo.find_by_ids.return_value = {
        followed_user_id: SimpleNamespace(main_channel_id=profile_channel_id)
    }
    svc.channels_repo.list_by_ids.return_value = [direct_channel, profile_channel]
    svc.authorization.can.side_effect = [False, True]
    svc.messages.get_feed_for_containers.return_value = ([], "next-page")

    items, next_cursor = await svc.home(
        user_id=VIEWER,
        limit=10,
        cursor="current-page",
    )

    assert items == []
    assert next_cursor == "next-page"
    svc.users_repo.find_by_ids.assert_awaited_once_with([followed_user_id])
    svc.channels_repo.list_by_ids.assert_awaited_once_with(
        [direct_channel_id, profile_channel_id]
    )
    svc.authorization.prime_channel_access.assert_awaited_once_with(
        user_id=VIEWER,
        channels=[direct_channel, profile_channel],
        active_follows=follows,
    )
    svc.messages.get_feed_for_containers.assert_awaited_once_with(
        container_type="channel",
        container_ids=[profile_channel_id],
        limit=10,
        cursor="current-page",
    )


@pytest.mark.asyncio
async def test_home_large_follow_set_uses_batched_repository_queries():
    svc = make_service()
    followed_user_ids = [f"{index:024x}" for index in range(1, 1001)]
    direct_channel_ids = [f"{index:024x}" for index in range(1001, 2001)]
    profile_channel_ids = [f"{index:024x}" for index in range(2001, 3001)]
    follows = [
        *[
            make_follow(target_type="user", target_id=user_id)
            for user_id in followed_user_ids
        ],
        *[
            make_follow(target_type="channel", target_id=channel_id)
            for channel_id in direct_channel_ids
        ],
    ]
    channels = [
        make_channel(channel_id=channel_id)
        for channel_id in [*direct_channel_ids, *profile_channel_ids]
    ]
    svc.relationships_repo.list_active_follows_for_user.return_value = follows
    svc.users_repo.find_by_ids.return_value = {
        user_id: SimpleNamespace(main_channel_id=profile_channel_id)
        for user_id, profile_channel_id in zip(
            followed_user_ids, profile_channel_ids, strict=True
        )
    }
    svc.channels_repo.list_by_ids.return_value = channels
    svc.messages.get_feed_for_containers.return_value = ([], None)

    await svc.home(user_id=VIEWER, limit=20, cursor=None)

    svc.relationships_repo.list_active_follows_for_user.assert_awaited_once_with(
        user_id=VIEWER
    )
    svc.users_repo.find_by_ids.assert_awaited_once_with(followed_user_ids)
    svc.channels_repo.list_by_ids.assert_awaited_once_with(
        [*direct_channel_ids, *profile_channel_ids]
    )
    svc.authorization.prime_channel_access.assert_awaited_once()
    assert svc.authorization.can.await_count == 2000
    svc.messages.get_feed_for_containers.assert_awaited_once_with(
        container_type="channel",
        container_ids=[channel.str_id for channel in channels],
        limit=20,
        cursor=None,
    )


@pytest.mark.asyncio
async def test_to_feed_post_maps_content_media_reactions_author():
    svc = make_service()
    now = datetime.now(UTC)
    media = MediaMeta(
        kind="image",
        storage="local",
        key="k",
        url="http://x/i.png",
        mime="image/png",
        size_bytes=10,
    )
    doc = MessageDoc(
        id="507f1f77bcf86cd7994390bb",
        container_type="channel",
        container_id="507f1f77bcf86cd7994390aa",
        sender_id=OWNER,
        type="text",
        content=MessageContent(
            plaintext=MessagePlaintext(text="hi there", media=media)
        ),
        reactions=[
            MessageReactionGroup(emoji="👍", user_ids=[VIEWER], count=1, updated_at=now)
        ],
        thread_reply_count=2,
        is_thread_root=True,
        created_at=now,
        updated_at=now,
    )
    author = SimpleNamespace(username="alice", display_name="Alice", avatar=None)

    post = svc._to_feed_post(doc, {OWNER: author})

    assert post.text == "hi there"
    assert post.channel_id == "507f1f77bcf86cd7994390aa"
    assert post.attachments[0].url == "http://x/i.png"
    assert post.comment_count == 2
    assert post.has_thread is True
    assert post.author.display_name == "Alice"
    assert post.reactions[0].emoji == "👍"
