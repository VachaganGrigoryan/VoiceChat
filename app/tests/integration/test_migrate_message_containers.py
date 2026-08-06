from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from bson import ObjectId
from pymongo import AsyncMongoClient

from app.core.config import settings
from app.db.collections import COL_CONVERSATIONS, COL_MESSAGES
from app.scripts.migrate_message_containers import run_migration


def _legacy_message(
    *,
    conversation_id: str,
    sender_id: str,
    text: str,
    created_at: datetime,
    thread_root_id: str | None = None,
) -> dict:
    """A pre-migration message: `conversation_id` only, no container."""
    return {
        "_id": ObjectId(),
        "conversation_id": conversation_id,
        "sender_id": sender_id,
        "type": "text",
        "content": {
            "encryption": "none",
            "type": "text",
            "plaintext": {"text": text},
            "attachments": [],
        },
        "hidden_for_user_ids": [],
        "thread_root_id": thread_root_id,
        "reactions": [],
        "state": "sent",
        "created_at": created_at,
        "updated_at": created_at,
    }


async def _reset(db) -> None:
    await db[COL_MESSAGES].delete_many({})
    await db[COL_CONVERSATIONS].delete_many({})


@pytest.mark.asyncio
async def test_dry_run_reports_backfill_without_writing(app_lifecycle):
    db = AsyncMongoClient(settings.mongo_uri)[settings.mongo_db]
    await _reset(db)
    now = datetime.now(UTC)
    conversation_id = str(ObjectId())
    sender_id = str(ObjectId())

    await db[COL_MESSAGES].insert_many(
        [
            _legacy_message(
                conversation_id=conversation_id,
                sender_id=sender_id,
                text=f"m{i}",
                created_at=now + timedelta(seconds=i),
            )
            for i in range(3)
        ]
    )

    stats = await run_migration(db, apply=False)

    assert stats.messages_scanned == 3
    assert stats.messages_backfilled == 3
    assert stats.conversation_id_unset == 3
    assert await db[COL_MESSAGES].count_documents({"container_id": {"$ne": None}}) == 0
    assert (
        await db[COL_MESSAGES].count_documents({"conversation_id": conversation_id}) == 3
    )


@pytest.mark.asyncio
async def test_backfill_sets_conversation_container_and_drops_legacy_field(
    app_lifecycle,
):
    db = AsyncMongoClient(settings.mongo_uri)[settings.mongo_db]
    await _reset(db)
    now = datetime.now(UTC)
    conversation_id = str(ObjectId())
    other_conversation_id = str(ObjectId())
    sender_id = str(ObjectId())

    await db[COL_MESSAGES].insert_many(
        [
            _legacy_message(
                conversation_id=conversation_id,
                sender_id=sender_id,
                text="a",
                created_at=now,
            ),
            _legacy_message(
                conversation_id=conversation_id,
                sender_id=sender_id,
                text="b",
                created_at=now + timedelta(seconds=1),
            ),
            _legacy_message(
                conversation_id=other_conversation_id,
                sender_id=sender_id,
                text="c",
                created_at=now + timedelta(seconds=2),
            ),
        ]
    )

    stats = await run_migration(db, apply=True)

    assert stats.messages_backfilled == 3
    assert stats.conversation_id_unset == 3
    assert (
        await db[COL_MESSAGES].count_documents(
            {"container_type": "conversation", "container_id": conversation_id}
        )
        == 2
    )
    assert (
        await db[COL_MESSAGES].count_documents(
            {"container_type": "conversation", "container_id": other_conversation_id}
        )
        == 1
    )
    # Every message is accounted for, and the legacy pointer is gone.
    assert await db[COL_MESSAGES].count_documents({}) == 3
    assert await db[COL_MESSAGES].count_documents({"conversation_id": {"$exists": True}}) == 0


@pytest.mark.asyncio
async def test_flattening_a_thread_conversation_preserves_ordering(app_lifecycle):
    db = AsyncMongoClient(settings.mongo_uri)[settings.mongo_db]
    await _reset(db)
    now = datetime.now(UTC)
    parent_id = ObjectId()
    thread_id = ObjectId()
    sender_id = str(ObjectId())

    root = _legacy_message(
        conversation_id=str(parent_id),
        sender_id=sender_id,
        text="root",
        created_at=now,
    )
    await db[COL_MESSAGES].insert_one(root)
    root_message_id = str(root["_id"])

    # Two replies live in the promoted thread conversation, one still flat on
    # the parent — the migration must merge them into one ordered pool.
    await db[COL_MESSAGES].insert_many(
        [
            _legacy_message(
                conversation_id=str(parent_id),
                sender_id=sender_id,
                text="flat-reply",
                created_at=now + timedelta(seconds=1),
                thread_root_id=root_message_id,
            ),
            _legacy_message(
                conversation_id=str(thread_id),
                sender_id=sender_id,
                text="promoted-1",
                created_at=now + timedelta(seconds=2),
            ),
            _legacy_message(
                conversation_id=str(thread_id),
                sender_id=sender_id,
                text="promoted-2",
                created_at=now + timedelta(seconds=3),
            ),
        ]
    )
    await db[COL_CONVERSATIONS].insert_many(
        [
            {
                "_id": parent_id,
                "type": "group",
                "participant_ids": [sender_id],
                "created_by": sender_id,
                "title": "Parent",
                "created_at": now,
                "updated_at": now,
            },
            {
                "_id": thread_id,
                "type": "thread",
                "participant_ids": [sender_id],
                "created_by": sender_id,
                "parent_conversation_id": str(parent_id),
                "root_message_id": root_message_id,
                "created_at": now,
                "updated_at": now,
            },
        ]
    )

    stats = await run_migration(db, apply=True)

    assert stats.thread_conversations_scanned == 1
    assert stats.thread_messages_rehomed == 2
    assert stats.thread_conversations_deleted == 1
    assert await db[COL_CONVERSATIONS].count_documents({"type": "thread"}) == 0

    thread_items = (
        await db[COL_MESSAGES]
        .find({"container_id": str(parent_id), "thread_root_id": root_message_id})
        .sort([("created_at", 1)])
        .to_list(length=None)
    )
    assert [item["content"]["plaintext"]["text"] for item in thread_items] == [
        "flat-reply",
        "promoted-1",
        "promoted-2",
    ]
    assert all(item["container_type"] == "conversation" for item in thread_items)
    # The root stays a root on the parent container.
    root_doc = await db[COL_MESSAGES].find_one({"_id": root["_id"]})
    assert root_doc["container_id"] == str(parent_id)
    assert root_doc["thread_root_id"] is None


@pytest.mark.asyncio
async def test_migration_is_idempotent(app_lifecycle):
    db = AsyncMongoClient(settings.mongo_uri)[settings.mongo_db]
    await _reset(db)
    now = datetime.now(UTC)
    conversation_id = str(ObjectId())
    sender_id = str(ObjectId())

    await db[COL_MESSAGES].insert_one(
        _legacy_message(
            conversation_id=conversation_id,
            sender_id=sender_id,
            text="only",
            created_at=now,
        )
    )

    await run_migration(db, apply=True)
    second = await run_migration(db, apply=True)

    assert second.messages_backfilled == 0
    assert second.conversation_id_unset == 0
    assert (
        await db[COL_MESSAGES].count_documents(
            {"container_type": "conversation", "container_id": conversation_id}
        )
        == 1
    )


@pytest.mark.asyncio
async def test_keep_conversation_id_stops_before_the_cutover(app_lifecycle):
    db = AsyncMongoClient(settings.mongo_uri)[settings.mongo_db]
    await _reset(db)
    now = datetime.now(UTC)
    conversation_id = str(ObjectId())

    await db[COL_MESSAGES].insert_one(
        _legacy_message(
            conversation_id=conversation_id,
            sender_id=str(ObjectId()),
            text="staged",
            created_at=now,
        )
    )

    stats = await run_migration(db, apply=True, keep_conversation_id=True)

    assert stats.messages_backfilled == 1
    assert stats.conversation_id_unset == 0
    assert (
        await db[COL_MESSAGES].count_documents({"conversation_id": conversation_id}) == 1
    )
