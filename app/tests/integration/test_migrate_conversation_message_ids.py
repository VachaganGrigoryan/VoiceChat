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
from app.scripts.migrate_conversation_message_ids import run_migration


def _legacy_message(
    *,
    sender_id: str,
    receiver_id: str,
    text: str,
    created_at: datetime,
    conversation_id: str | None = None,
    status: str = "sent",
) -> dict:
    return {
        "_id": ObjectId(),
        "conversation_id": conversation_id or dm_key_for(sender_id, receiver_id),
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
async def test_migration_dry_run_reports_without_writes(app_lifecycle):
    db = AsyncMongoClient(settings.mongo_uri)[settings.mongo_db]
    user_a = str(ObjectId())
    user_b = str(ObjectId())
    created_at = datetime(2026, 3, 1, 10, 0, 0, tzinfo=UTC)
    legacy_key = dm_key_for(user_a, user_b)

    await db[COL_MESSAGES].insert_one(
        _legacy_message(
            sender_id=user_a,
            receiver_id=user_b,
            text="dry run",
            created_at=created_at,
        )
    )

    stats = await run_migration(db, apply=False)

    assert stats.conversation_keys_scanned == 1
    assert stats.conversations_created == 1
    assert stats.participants_created == 2
    assert stats.messages_rewritten == 1
    assert stats.messages_content_wrapped == 1
    assert await db[COL_CONVERSATIONS].count_documents({}) == 0
    message = await db[COL_MESSAGES].find_one({})
    assert message["conversation_id"] == legacy_key
    assert "content" not in message


@pytest.mark.asyncio
async def test_migration_rewrites_messages_and_is_idempotent(app_lifecycle):
    db = AsyncMongoClient(settings.mongo_uri)[settings.mongo_db]
    user_a = str(ObjectId())
    user_b = str(ObjectId())
    base = datetime(2026, 3, 2, 11, 0, 0, tzinfo=UTC)
    legacy_key = dm_key_for(user_a, user_b)

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

    first = await run_migration(db, apply=True)

    assert first.conversations_created == 1
    assert first.participants_created == 2
    assert first.messages_rewritten == 2
    assert first.messages_content_wrapped == 2

    conversation = await db[COL_CONVERSATIONS].find_one({"dm_key": legacy_key})
    assert conversation is not None
    conversation_id = str(conversation["_id"])
    assert conversation["last_message_preview"]["text"] == "second"

    assert await db[COL_MESSAGES].count_documents({"conversation_id": legacy_key}) == 0
    assert await db[COL_MESSAGES].count_documents({"conversation_id": conversation_id}) == 2
    wrapped = await db[COL_MESSAGES].find_one({"text": "first"})
    assert wrapped["content"]["plaintext"]["text"] == "first"

    participants = (
        await db[COL_CONVERSATION_PARTICIPANTS]
        .find({"conversation_id": conversation_id})
        .to_list(length=10)
    )
    assert len(participants) == 2
    assert {participant["user_id"] for participant in participants} == {user_a, user_b}

    second = await run_migration(db, apply=True)

    assert second.conversations_created == 0
    assert second.participants_created == 0
    assert second.messages_rewritten == 0
    assert second.messages_content_wrapped == 0
    assert second.conversations_already_canonical == 1


@pytest.mark.asyncio
async def test_migration_skips_ambiguous_non_dm_keys(app_lifecycle):
    db = AsyncMongoClient(settings.mongo_uri)[settings.mongo_db]
    user_a = str(ObjectId())
    user_b = str(ObjectId())
    user_c = str(ObjectId())
    legacy_key = "legacy-group-like-key"
    base = datetime(2026, 3, 3, 12, 0, 0, tzinfo=UTC)

    await db[COL_MESSAGES].insert_many(
        [
            _legacy_message(
                sender_id=user_a,
                receiver_id=user_b,
                text="one",
                created_at=base,
                conversation_id=legacy_key,
            ),
            _legacy_message(
                sender_id=user_c,
                receiver_id=user_a,
                text="two",
                created_at=base + timedelta(minutes=1),
                conversation_id=legacy_key,
            ),
        ]
    )

    stats = await run_migration(db, apply=True)

    assert stats.conversations_skipped_non_dm == 1
    assert stats.skipped_keys == [legacy_key]
    assert await db[COL_CONVERSATIONS].count_documents({}) == 0
    assert await db[COL_MESSAGES].count_documents({"conversation_id": legacy_key}) == 2
