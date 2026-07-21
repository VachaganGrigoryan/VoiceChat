from __future__ import annotations

from datetime import UTC, datetime

import pytest
from bson import ObjectId
from pymongo import ASCENDING, AsyncMongoClient
from pymongo.errors import OperationFailure

from app.core.config import settings
from app.db.collections import COL_MESSAGES
from app.scripts.repair_call_message_content import run_migration


async def _drop_call_content_index(db) -> None:
    try:
        await db[COL_MESSAGES].drop_index("ux_messages_content_call_call_id")
    except OperationFailure:
        pass


async def _drop_legacy_call_index(db) -> None:
    try:
        await db[COL_MESSAGES].drop_index("ux_messages_call_call_id")
    except OperationFailure:
        pass


def _call_payload(*, call_id: str) -> dict:
    now = datetime(2026, 3, 10, 12, 0, 0, tzinfo=UTC)
    return {
        "call_id": call_id,
        "type": "audio",
        "status": "ended",
        "caller_user_id": str(ObjectId()),
        "callee_user_id": str(ObjectId()),
        "started_at": now,
        "answered_at": now,
        "ended_at": now,
        "duration_ms": 0,
    }


def _call_message(
    *,
    call_id: str | None,
    include_content: bool = False,
) -> dict:
    now = datetime(2026, 3, 10, 12, 0, 0, tzinfo=UTC)
    message = {
        "_id": ObjectId(),
        "conversation_id": str(ObjectId()),
        "sender_id": str(ObjectId()),
        "type": "call",
        "call": _call_payload(call_id=call_id) if call_id is not None else None,
        "created_at": now,
        "updated_at": now,
    }
    if include_content:
        message["content"] = {
            "encryption": "none",
            "type": "call",
            "plaintext": {
                "text": None,
                "media": None,
                "call": _call_payload(call_id=call_id) if call_id is not None else None,
            },
            "ciphertext": None,
            "envelope": None,
        }
    return message


@pytest.mark.asyncio
async def test_repair_call_content_dry_run_reports_without_writes(app_lifecycle):
    db = AsyncMongoClient(settings.mongo_uri)[settings.mongo_db]
    await _drop_call_content_index(db)
    await db[COL_MESSAGES].insert_one(_call_message(call_id="call-1"))

    stats = await run_migration(db, apply=False)

    assert stats.call_messages_scanned == 1
    assert stats.content_repaired == 1
    assert stats.skipped_ids == []
    message = await db[COL_MESSAGES].find_one({})
    assert "content" not in message


@pytest.mark.asyncio
async def test_repair_call_content_applies_and_is_idempotent(app_lifecycle):
    db = AsyncMongoClient(settings.mongo_uri)[settings.mongo_db]
    await _drop_call_content_index(db)
    await db[COL_MESSAGES].insert_one(_call_message(call_id="call-1"))

    first = await run_migration(db, apply=True)

    assert first.content_repaired == 1
    message = await db[COL_MESSAGES].find_one({})
    assert message["content"]["type"] == "call"
    assert message["content"]["plaintext"]["call"]["call_id"] == "call-1"

    second = await run_migration(db, apply=True)

    assert second.already_valid == 1
    assert second.content_repaired == 0


@pytest.mark.asyncio
async def test_repair_call_content_blocks_unresolved_rows(app_lifecycle):
    db = AsyncMongoClient(settings.mongo_uri)[settings.mongo_db]
    await _drop_call_content_index(db)
    message = _call_message(call_id=None)
    await db[COL_MESSAGES].insert_one(message)

    stats = await run_migration(db, apply=True)

    assert stats.skipped_missing_call_payload == 1
    assert stats.skipped_ids == [str(message["_id"])]
    stored = await db[COL_MESSAGES].find_one({})
    assert "content" not in stored


@pytest.mark.asyncio
async def test_repair_call_content_blocks_duplicate_call_ids(app_lifecycle):
    db = AsyncMongoClient(settings.mongo_uri)[settings.mongo_db]
    await _drop_call_content_index(db)
    await _drop_legacy_call_index(db)
    first = _call_message(call_id="call-1")
    second = _call_message(call_id="call-1")
    await db[COL_MESSAGES].insert_many([first, second])

    stats = await run_migration(db, apply=True)

    assert stats.duplicate_call_ids == {
        "call-1": [str(first["_id"]), str(second["_id"])]
    }
    assert stats.writes_blocked is True
    assert await db[COL_MESSAGES].count_documents({"content": {"$exists": True}}) == 0


@pytest.mark.asyncio
async def test_repair_call_content_allows_strict_index_creation(app_lifecycle):
    db = AsyncMongoClient(settings.mongo_uri)[settings.mongo_db]
    await _drop_call_content_index(db)
    await db[COL_MESSAGES].insert_many(
        [_call_message(call_id="call-1"), _call_message(call_id="call-2")]
    )

    stats = await run_migration(db, apply=True)

    assert stats.content_repaired == 2
    await db[COL_MESSAGES].create_index(
        [("content.plaintext.call.call_id", ASCENDING)],
        unique=True,
        partialFilterExpression={"type": "call"},
        name="ux_messages_content_call_call_id",
    )
