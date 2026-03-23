from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.modules.messages.hydration import MessageHydrator
from app.modules.messages.schemas import (
    ConversationItem,
    ConversationLastMessage,
    ConversationPeer,
    MessageDoc,
    StickerMessageRef,
)


class FakeStickerResolver:
    def __init__(self, media_by_sticker_id: dict[str, dict]):
        self.media_by_sticker_id = media_by_sticker_id
        self.calls: list[list[str]] = []

    async def get_message_sticker_media_map(self, *, sticker_ids: list[str]) -> dict[str, dict]:
        self.calls.append(sticker_ids)
        return {
            sticker_id: self.media_by_sticker_id[sticker_id]
            for sticker_id in sticker_ids
            if sticker_id in self.media_by_sticker_id
        }


def _sticker_ref(sticker_id: str) -> StickerMessageRef:
    return StickerMessageRef(
        sticker_id=sticker_id,
        pack_id="pack-1",
        pack_slug="funny_cats",
        sticker_slug="party_cat",
        emoji="🎉",
        version=1,
    )


def _message(message_id: str, sticker_id: str, *, with_media: bool = False) -> MessageDoc:
    media = None
    if with_media:
        media = {
            "storage": "local",
            "key": f"stickers/{sticker_id}.webp",
            "url": f"/media/stickers/{sticker_id}.webp",
            "mime": "image/webp",
            "size_bytes": 128,
            "duration_ms": None,
        }

    return MessageDoc(
        id=message_id,
        conversation_id="c1",
        sender_id="u1",
        receiver_id="u2",
        type="sticker",
        text=None,
        media=media,
        sticker=_sticker_ref(sticker_id),
        status="sent",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


@pytest.mark.asyncio
async def test_message_hydrator_hydrates_messages_in_one_batch():
    resolver = FakeStickerResolver(
        {
            "stk-1": {
                "storage": "local",
                "key": "stickers/stk-1.webp",
                "url": "/media/stickers/stk-1.webp",
                "mime": "image/webp",
                "size_bytes": 128,
                "duration_ms": None,
            }
        }
    )
    hydrator = MessageHydrator(resolver)

    messages = [
        _message("msg-1", "stk-1"),
        _message("msg-2", "stk-1"),
    ]

    hydrated = await hydrator.hydrate_messages(messages)

    assert resolver.calls == [["stk-1"]]
    assert hydrated[0].media is not None
    assert hydrated[0].media.key == "stickers/stk-1.webp"
    assert hydrated[1].media is not None
    assert hydrated[1].media.key == "stickers/stk-1.webp"


@pytest.mark.asyncio
async def test_message_hydrator_hydrates_conversation_items():
    resolver = FakeStickerResolver(
        {
            "stk-2": {
                "storage": "local",
                "key": "stickers/stk-2.webp",
                "url": "/media/stickers/stk-2.webp",
                "mime": "image/webp",
                "size_bytes": 256,
                "duration_ms": None,
            }
        }
    )
    hydrator = MessageHydrator(resolver)

    items = [
        ConversationItem(
            conversation_id="c1",
            peer_user=ConversationPeer(id="u2"),
            last_message=ConversationLastMessage(
                id="msg-3",
                type="sticker",
                text=None,
                media=None,
                sticker=_sticker_ref("stk-2"),
                status="sent",
                created_at=datetime.now(UTC),
            ),
            last_message_at=datetime.now(UTC),
            unread_count=0,
        )
    ]

    hydrated = await hydrator.hydrate_conversation_items(items)

    assert resolver.calls == [["stk-2"]]
    assert hydrated[0].last_message.media is not None
    assert hydrated[0].last_message.media.key == "stickers/stk-2.webp"


@pytest.mark.asyncio
async def test_message_hydrator_keeps_message_when_sticker_media_missing():
    resolver = FakeStickerResolver({})
    hydrator = MessageHydrator(resolver)

    message = _message("msg-4", "missing")

    hydrated = await hydrator.hydrate_message(message)

    assert resolver.calls == [["missing"]]
    assert hydrated.media is None
    assert hydrated.sticker is not None
