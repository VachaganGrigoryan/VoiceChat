from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from bson import ObjectId
from pymongo import AsyncMongoClient

from app.core.config import settings
from app.db.collections import (
    COL_CONVERSATION_PARTICIPANTS,
    COL_CONVERSATIONS,
    COL_MESSAGES,
)
from app.modules.conversations.repository.helpers import dm_key_for
from app.modules.messages.repository.mappers import to_message_doc
from app.db.models import MessageDocument
from app.scripts.backfill_conversations import run_backfill


def _legacy_message(
    *, sender_id: str, receiver_id: str, text: str, created_at: datetime, status="sent"
) -> dict:
    # A pre-envelope message: no `content` field, derived conversation_id.
    return {
        "_id": ObjectId(),
        "conversation_id": dm_key_for(sender_id, receiver_id),
        "sender_id": sender_id,
        "receiver_id": receiver_id,
        "type": "text",
        "text": text,
        "media": None,
        "call": None,
        "status": status,
        "hidden_for_user_ids": [],
        "thread_root_id": None,
        "reactions": [],
        "created_at": created_at,
        "updated_at": created_at,
    }


@pytest.mark.asyncio
async def test_backfill_creates_conversation_and_participants_and_wraps_content(
    app_lifecycle,
):
    db = AsyncMongoClient(settings.mongo_uri)[settings.mongo_db]
    user_a = str(ObjectId())
    user_b = str(ObjectId())
    base = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)

    await db[COL_MESSAGES].insert_many(
        [
            _legacy_message(
                sender_id=user_a,
                receiver_id=user_b,
                text="first",
                created_at=base,
                status="read",
            ),
            _legacy_message(
                sender_id=user_b,
                receiver_id=user_a,
                text="second",
                created_at=base + timedelta(minutes=1),
            ),
        ]
    )

    # Dry run makes no writes.
    dry = await run_backfill(db, apply=False)
    assert dry.conversations_created == 1
    assert await db[COL_CONVERSATIONS].count_documents({}) == 0

    # Apply creates the entities and wraps content.
    stats = await run_backfill(db, apply=True)
    assert stats.conversations_created == 1
    assert stats.participants_created == 2
    assert stats.messages_content_wrapped == 2

    conversation = await db[COL_CONVERSATIONS].find_one({})
    assert conversation is not None
    assert conversation["type"] == "dm"
    assert conversation["dm_key"] == dm_key_for(user_a, user_b)
    assert sorted(conversation["participant_ids"]) == sorted([user_a, user_b])
    assert conversation["last_message_preview"]["text"] == "second"

    participants = (
        await db[COL_CONVERSATION_PARTICIPANTS]
        .find({"conversation_id": str(conversation["_id"])})
        .to_list(length=10)
    )
    assert len(participants) == 2

    # Content wrap: the message now carries the envelope, still deserializes, and
    # the mapper reads the wrapped plaintext.
    wrapped = await db[COL_MESSAGES].find_one({"text": "first"})
    assert wrapped["content"]["encryption"] == "none"
    assert wrapped["content"]["plaintext"]["text"] == "first"
    doc = MessageDocument.model_validate(wrapped)
    assert to_message_doc(doc).content.plaintext.text == "first"


@pytest.mark.asyncio
async def test_backfill_is_idempotent(app_lifecycle):
    db = AsyncMongoClient(settings.mongo_uri)[settings.mongo_db]
    user_a = str(ObjectId())
    user_b = str(ObjectId())
    base = datetime(2026, 2, 1, 9, 0, 0, tzinfo=UTC)

    await db[COL_MESSAGES].insert_one(
        _legacy_message(
            sender_id=user_a, receiver_id=user_b, text="hi", created_at=base
        )
    )

    await run_backfill(db, apply=True)
    second = await run_backfill(db, apply=True)

    assert second.conversations_created == 0
    assert second.conversations_skipped_existing == 1
    assert second.participants_created == 0
    assert second.messages_content_wrapped == 0
    assert await db[COL_CONVERSATIONS].count_documents({}) == 1
    assert await db[COL_CONVERSATION_PARTICIPANTS].count_documents({}) == 2
