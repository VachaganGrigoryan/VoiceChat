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


def make_channel(*, read_policy: str = "members", created_by: str = OWNER):
    return SimpleNamespace(
        type="channel",
        created_by=created_by,
        read_policy=read_policy,
        str_id="507f1f77bcf86cd7994390aa",
    )


def make_service(*, participant=None, has_permission=False):
    conversations_repo = AsyncMock()
    conversations_repo.get_participant.return_value = participant
    pings = AsyncMock()
    pings.has_chat_permission.return_value = has_permission
    return FeedsService(
        conversations_repo=conversations_repo,
        messages_service=AsyncMock(),
        messages_repo=AsyncMock(),
        pings_service=pings,
        users_repo=AsyncMock(),
    )


@pytest.mark.asyncio
async def test_owner_can_view():
    svc = make_service()
    assert await svc._can_view_channel(viewer_id=OWNER, channel=make_channel()) is True


@pytest.mark.asyncio
async def test_member_can_view_regardless_of_policy():
    svc = make_service(participant=SimpleNamespace(role="member"))
    channel = make_channel(read_policy="members")
    assert await svc._can_view_channel(viewer_id=VIEWER, channel=channel) is True


@pytest.mark.asyncio
async def test_public_policy_allows_any_viewer():
    svc = make_service()
    channel = make_channel(read_policy="public")
    assert await svc._can_view_channel(viewer_id=VIEWER, channel=channel) is True


@pytest.mark.asyncio
async def test_contacts_policy_requires_accepted_ping():
    allowed = make_service(has_permission=True)
    denied = make_service(has_permission=False)
    channel = make_channel(read_policy="contacts")
    assert await allowed._can_view_channel(viewer_id=VIEWER, channel=channel) is True
    assert await denied._can_view_channel(viewer_id=VIEWER, channel=channel) is False


@pytest.mark.asyncio
async def test_members_policy_denies_outsider():
    svc = make_service()
    channel = make_channel(read_policy="members")
    assert await svc._can_view_channel(viewer_id=VIEWER, channel=channel) is False
    with pytest.raises(AppError):
        await svc.assert_can_view_channel(viewer_id=VIEWER, channel=channel)


@pytest.mark.asyncio
async def test_to_feed_post_maps_content_media_reactions_author():
    svc = make_service()
    now = datetime.now(UTC)
    media = MediaMeta(
        kind="image", storage="local", key="k", url="http://x/i.png", mime="image/png", size_bytes=10
    )
    doc = MessageDoc(
        id="507f1f77bcf86cd7994390bb",
        conversation_id="507f1f77bcf86cd7994390aa",
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
