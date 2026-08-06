from __future__ import annotations

from datetime import UTC, datetime

import pytest
from bson import ObjectId
from pymongo import AsyncMongoClient

from app.core.config import settings
from app.db.collections import (
    COL_CALLS,
    COL_CONVERSATIONS,
    COL_INVITE_LINKS,
    COL_MESSAGES,
    COL_NOTIFICATIONS,
    COL_POLLS,
    COL_SAVED_MESSAGES,
)
from app.scripts.migrate_dependent_models import run_migration

_COLLECTIONS = (
    COL_CALLS,
    COL_CONVERSATIONS,
    COL_INVITE_LINKS,
    COL_MESSAGES,
    COL_NOTIFICATIONS,
    COL_POLLS,
    COL_SAVED_MESSAGES,
)


async def _reset(db) -> None:
    for collection in _COLLECTIONS:
        await db[collection].delete_many({})


async def _seed_legacy_documents(db) -> dict[str, ObjectId]:
    now = datetime.now(UTC)
    user_id = ObjectId()
    peer_id = ObjectId()
    conversation_id = ObjectId()
    poll_id = ObjectId()
    poll_message_id = ObjectId()

    await db[COL_CONVERSATIONS].insert_one(
        {
            "_id": conversation_id,
            "type": "dm",
            "participant_ids": [str(user_id), str(peer_id)],
            "created_at": now,
            "updated_at": now,
        }
    )
    await db[COL_MESSAGES].insert_one(
        {
            "_id": poll_message_id,
            "container_type": "conversation",
            "container_id": str(conversation_id),
            "sender_id": str(user_id),
            "content": {
                "plaintext": {"poll_ref": {"poll_id": str(poll_id), "question": "Q"}}
            },
            "created_at": now,
            "updated_at": now,
        }
    )
    await db[COL_POLLS].insert_one(
        {
            "_id": poll_id,
            "conversation_id": str(conversation_id),
            "created_at": now,
            "updated_at": now,
        }
    )
    await db[COL_SAVED_MESSAGES].insert_one(
        {
            "_id": ObjectId(),
            "user_id": str(user_id),
            "message_id": str(poll_message_id),
            "conversation_id": str(conversation_id),
            "saved_at": now,
            "created_at": now,
            "updated_at": now,
        }
    )
    await db[COL_CALLS].insert_one(
        {
            "_id": ObjectId(),
            "caller_user_id": str(user_id),
            "callee_user_id": str(peer_id),
            "participant_user_ids": [str(user_id), str(peer_id)],
            "created_at": now,
            "updated_at": now,
        }
    )
    await db[COL_NOTIFICATIONS].insert_one(
        {
            "_id": ObjectId(),
            "user_id": str(peer_id),
            "kind": "ping_received",
            "source_type": "ping",
            "data": {"peer_user_id": str(user_id)},
            "created_at": now,
            "updated_at": now,
        }
    )
    await db[COL_INVITE_LINKS].insert_one(
        {
            "_id": ObjectId(),
            "target_type": "conversation",
            "target_id": str(conversation_id),
            "created_by": str(user_id),
            "code": "legacy-code",
            "use_count": 2,
            "requires_approval": True,
            "created_at": now,
            "updated_at": now,
        }
    )
    return {
        "conversation_id": conversation_id,
        "poll_id": poll_id,
        "poll_message_id": poll_message_id,
    }


@pytest.mark.asyncio
async def test_dry_run_reports_all_dependent_shapes_without_writing(app_lifecycle):
    db = AsyncMongoClient(settings.mongo_uri)[settings.mongo_db]
    await _reset(db)
    ids = await _seed_legacy_documents(db)

    stats = await run_migration(db, apply=False)

    assert stats.polls_planned == 1
    assert stats.saved_messages_planned == 1
    assert stats.calls_planned == 1
    assert stats.notifications_planned == 1
    assert stats.invite_links_planned == 1
    assert stats.documents_written == 0
    poll = await db[COL_POLLS].find_one({"_id": ids["poll_id"]})
    assert poll is not None
    assert "message_id" not in poll
    assert "conversation_id" in poll


@pytest.mark.asyncio
async def test_apply_migrates_dependent_shapes_and_is_idempotent(app_lifecycle):
    db = AsyncMongoClient(settings.mongo_uri)[settings.mongo_db]
    await _reset(db)
    ids = await _seed_legacy_documents(db)

    stats = await run_migration(db, apply=True)

    assert stats.documents_written == 5
    assert stats.skipped_ids == []

    poll = await db[COL_POLLS].find_one({"_id": ids["poll_id"]})
    assert poll["message_id"] == str(ids["poll_message_id"])
    assert "conversation_id" not in poll

    saved = await db[COL_SAVED_MESSAGES].find_one({})
    assert "conversation_id" not in saved

    call = await db[COL_CALLS].find_one({})
    assert call["conversation_id"] == str(ids["conversation_id"])

    notification = await db[COL_NOTIFICATIONS].find_one({})
    assert notification["kind"] == "connection_request"
    assert notification["resource_type"] == "user"
    assert "source_type" not in notification

    invite = await db[COL_INVITE_LINKS].find_one({})
    assert invite["uses"] == 2
    assert invite["approval_required"] is True
    assert invite["role_ids"] == []
    assert "use_count" not in invite
    assert "requires_approval" not in invite

    second = await run_migration(db, apply=True)
    assert second.saved_messages_planned == 0
