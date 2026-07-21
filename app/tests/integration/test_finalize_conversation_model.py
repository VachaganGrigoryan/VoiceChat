from __future__ import annotations

from datetime import UTC, datetime

import pytest
from bson import ObjectId
from pymongo import AsyncMongoClient

from app.core.config import settings
from app.db.collections import COL_CONVERSATION_PARTICIPANTS, COL_CONVERSATIONS
from app.scripts.finalize_conversation_model import run_finalize


@pytest.mark.asyncio
async def test_finalize_dry_run_reports_without_writes(app_lifecycle):
    db = AsyncMongoClient(settings.mongo_uri)[settings.mongo_db]
    conversation_id = ObjectId()
    user_a = str(ObjectId())
    user_b = str(ObjectId())
    now = datetime(2026, 7, 21, 12, 0, 0, tzinfo=UTC)

    await db[COL_CONVERSATIONS].insert_one(
        {
            "_id": conversation_id,
            "type": "dm",
            "participant_ids": [user_a, user_b],
            "created_by": user_a,
            "encryption": "none",
            "created_at": now,
            "updated_at": now,
        }
    )
    await db[COL_CONVERSATION_PARTICIPANTS].insert_many(
        [
            {
                "conversation_id": str(conversation_id),
                "user_id": user_a,
                "role": "member",
                "joined_at": now,
                "muted": True,
                "hidden": False,
                "created_at": now,
                "updated_at": now,
            },
            {
                "conversation_id": str(conversation_id),
                "user_id": user_b,
                "role": "member",
                "joined_at": now,
                "muted": False,
                "hidden": False,
                "created_at": now,
                "updated_at": now,
            },
        ]
    )

    stats = await run_finalize(db, apply=False)

    assert stats.conversations_scanned == 1
    assert stats.conversations_defaults_set == 1
    assert stats.conversations_member_count_set == 1
    assert stats.participants_scanned == 2
    assert stats.participants_notification_level_set == 2

    conversation = await db[COL_CONVERSATIONS].find_one({"_id": conversation_id})
    assert "visibility" not in conversation
    assert "posting_policy" not in conversation
    assert "member_count" not in conversation
    assert (
        await db[COL_CONVERSATION_PARTICIPANTS].count_documents(
            {"notification_level": {"$exists": True}}
        )
        == 0
    )


@pytest.mark.asyncio
async def test_finalize_applies_defaults_and_is_idempotent(app_lifecycle):
    db = AsyncMongoClient(settings.mongo_uri)[settings.mongo_db]
    conversation_id = ObjectId()
    user_a = str(ObjectId())
    user_b = str(ObjectId())
    now = datetime(2026, 7, 21, 13, 0, 0, tzinfo=UTC)

    await db[COL_CONVERSATIONS].insert_one(
        {
            "_id": conversation_id,
            "type": "group",
            "participant_ids": [user_a, user_b],
            "created_by": user_a,
            "encryption": "none",
            "created_at": now,
            "updated_at": now,
        }
    )
    await db[COL_CONVERSATION_PARTICIPANTS].insert_many(
        [
            {
                "conversation_id": str(conversation_id),
                "user_id": user_a,
                "role": "owner",
                "joined_at": now,
                "muted": True,
                "hidden": False,
                "created_at": now,
                "updated_at": now,
            },
            {
                "conversation_id": str(conversation_id),
                "user_id": user_b,
                "role": "member",
                "joined_at": now,
                "muted": False,
                "hidden": False,
                "created_at": now,
                "updated_at": now,
            },
        ]
    )

    first = await run_finalize(db, apply=True)
    second = await run_finalize(db, apply=True)

    assert first.conversations_defaults_set == 1
    assert first.conversations_member_count_set == 1
    assert first.participants_notification_level_set == 2
    assert second.conversations_defaults_set == 0
    assert second.conversations_member_count_set == 0
    assert second.participants_notification_level_set == 0

    conversation = await db[COL_CONVERSATIONS].find_one({"_id": conversation_id})
    assert conversation["visibility"] == "private"
    assert conversation["posting_policy"] == "everyone"
    assert conversation["member_count"] == 2

    muted_participant = await db[COL_CONVERSATION_PARTICIPANTS].find_one(
        {"user_id": user_a}
    )
    unmuted_participant = await db[COL_CONVERSATION_PARTICIPANTS].find_one(
        {"user_id": user_b}
    )
    assert muted_participant["notification_level"] == "none"
    assert unmuted_participant["notification_level"] == "all"
