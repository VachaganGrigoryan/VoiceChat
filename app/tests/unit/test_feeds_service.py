from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core.errors import AppError
from app.modules.feeds.service import FeedsService
from app.modules.messages.schemas import (
    MediaMeta,
    MessageContent,
    MessageDoc,
    MessagePlaintext,
    MessageReactionGroup,
)

OWNER = "507f1f77bcf86cd799439011"
VIEWER = "507f1f77bcf86cd799439012"


def make_channel(*, visibility: str = "public"):
    return SimpleNamespace(
        visibility=visibility,
        str_id="507f1f77bcf86cd7994390aa",
    )


def make_service(*, allowed: bool = True):
    authorization = AsyncMock()
    authorization.can.return_value = allowed
    return FeedsService(
        channels_repo=AsyncMock(),
        messages_service=AsyncMock(),
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
async def test_to_feed_post_maps_content_media_reactions_author():
    svc = make_service()
    now = datetime.now(UTC)
    media = MediaMeta(
        kind="image", storage="local", key="k", url="http://x/i.png", mime="image/png", size_bytes=10
    )
    doc = MessageDoc(
        id="507f1f77bcf86cd7994390bb",
        container_type="channel",
        container_id="507f1f77bcf86cd7994390aa",
        sender_id=OWNER,
        type="text",
        content=MessageContent(plaintext=MessagePlaintext(text="hi there", media=media)),
        reactions=[MessageReactionGroup(emoji="👍", user_ids=[VIEWER], count=1, updated_at=now)],
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
