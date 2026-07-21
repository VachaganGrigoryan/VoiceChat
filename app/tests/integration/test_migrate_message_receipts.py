from __future__ import annotations

from datetime import UTC, datetime

import pytest
from bson import ObjectId
from pymongo import AsyncMongoClient

from app.core.config import settings
from app.db.collections import (
    COL_CONVERSATION_PARTICIPANTS,
    COL_CONVERSATIONS,
    COL_MESSAGE_RECEIPTS,
    COL_MESSAGES,
)
from app.scripts.migrate_message_receipts import run_migration


@pytest.mark.asyncio
async def test_receipt_migration_dry_run_reports_without_writes(app_lifecycle):
    db = AsyncMongoClient(settings.mongo_uri)[settings.mongo_db]
    conversation_id = ObjectId()
    sender_id = str(ObjectId())
    receiver_id = str(ObjectId())
    now = datetime(2026, 3, 12, 12, 0, 0, tzinfo=UTC)
    await db[COL_CONVERSATIONS].insert_one(
        {
            "_id": conversation_id,
            "type": "dm",
            "participant_ids": [sender_id, receiver_id],
            "created_by": sender_id,
            "encryption": "none",
            "created_at": now,
            "updated_at": now,
        }
    )
    await db[COL_MESSAGES].insert_one(
        {
            "_id": ObjectId(),
            "conversation_id": str(conversation_id),
            "sender_id": sender_id,
            "receiver_id": receiver_id,
            "type": "text",
            "status": "read",
            "delivered_at": now,
            "read_at": now,
            "created_at": now,
            "updated_at": now,
        }
    )

    stats = await run_migration(db, apply=False)

    assert stats.messages_scanned == 1
    assert stats.receipts_planned == 1
    assert await db[COL_MESSAGE_RECEIPTS].count_documents({}) == 0


@pytest.mark.asyncio
async def test_receipt_migration_applies_and_is_idempotent(app_lifecycle):
    db = AsyncMongoClient(settings.mongo_uri)[settings.mongo_db]
    conversation_id = ObjectId()
    sender_id = str(ObjectId())
    receiver_id = str(ObjectId())
    now = datetime(2026, 3, 12, 12, 0, 0, tzinfo=UTC)
    await db[COL_CONVERSATIONS].insert_one(
        {
            "_id": conversation_id,
            "type": "dm",
            "participant_ids": [sender_id, receiver_id],
            "created_by": sender_id,
            "encryption": "none",
            "created_at": now,
            "updated_at": now,
        }
    )
    await db[COL_CONVERSATION_PARTICIPANTS].insert_one(
        {
            "conversation_id": str(conversation_id),
            "user_id": receiver_id,
            "role": "member",
            "joined_at": now,
            "last_read_at": now,
            "last_read_message_id": None,
            "muted": False,
            "hidden": False,
            "created_at": now,
            "updated_at": now,
        }
    )
    message_id = ObjectId()
    await db[COL_MESSAGES].insert_one(
        {
            "_id": message_id,
            "conversation_id": str(conversation_id),
            "sender_id": sender_id,
            "receiver_id": receiver_id,
            "type": "text",
            "status": "delivered",
            "delivered_at": now,
            "created_at": now,
            "updated_at": now,
        }
    )

    first = await run_migration(db, apply=True)
    second = await run_migration(db, apply=True)

    assert first.receipts_written >= 1
    assert second.receipts_written >= 0
    assert await db[COL_MESSAGE_RECEIPTS].count_documents({}) == 1
    receipt = await db[COL_MESSAGE_RECEIPTS].find_one(
        {"message_id": str(message_id), "user_id": receiver_id}
    )
    assert receipt is not None
    assert receipt["delivered_at"] is not None
    assert receipt["read_at"] is not None
