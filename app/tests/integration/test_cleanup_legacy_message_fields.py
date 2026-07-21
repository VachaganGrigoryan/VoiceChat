from __future__ import annotations

from datetime import UTC, datetime

import pytest
from bson import ObjectId
from pymongo import AsyncMongoClient
from pymongo.errors import OperationFailure

from app.core.config import settings
from app.db.collections import COL_CONVERSATIONS, COL_MESSAGES
from app.scripts.cleanup_legacy_message_fields import run_cleanup


async def _drop_legacy_call_index(db) -> None:
    try:
        await db[COL_MESSAGES].drop_index("ux_messages_call_call_id")
    except OperationFailure:
        pass


def _conversation(*, conversation_id: ObjectId, user_a: str, user_b: str) -> dict:
    now = datetime(2026, 3, 10, 12, 0, 0, tzinfo=UTC)
    return {
        "_id": conversation_id,
        "type": "dm",
        "participant_ids": [user_a, user_b],
        "created_by": user_a,
        "title": None,
        "encryption": "none",
        "dm_key": f"{user_a}_{user_b}",
        "last_message_at": now,
        "last_message_preview": None,
        "created_at": now,
        "updated_at": now,
    }


def _message(
    *,
    conversation_id: str,
    sender_id: str,
    receiver_id: str,
    text: str,
    include_content: bool = True,
    message_type: str = "text",
    call_id: str | None = None,
) -> dict:
    now = datetime(2026, 3, 10, 12, 0, 0, tzinfo=UTC)
    call = (
        {
            "call_id": call_id,
            "type": "audio",
            "status": "ended",
            "caller_user_id": sender_id,
            "callee_user_id": receiver_id,
            "started_at": now,
            "answered_at": now,
            "ended_at": now,
            "duration_ms": 0,
        }
        if call_id is not None
        else None
    )
    message = {
        "_id": ObjectId(),
        "conversation_id": conversation_id,
        "sender_id": sender_id,
        "receiver_id": receiver_id,
        "type": message_type,
        "text": text,
        "media": None,
        "call": call,
        "status": "sent",
        "hidden_for_user_ids": [],
        "thread_root_id": None,
        "reactions": [],
        "created_at": now,
        "updated_at": now,
    }
    if include_content:
        message["content"] = {
            "encryption": "none",
            "type": message_type,
            "plaintext": {"text": text, "media": None, "call": call},
            "ciphertext": None,
            "envelope": None,
        }
    return message


@pytest.mark.asyncio
async def test_cleanup_dry_run_reports_without_writes(app_lifecycle):
    db = AsyncMongoClient(settings.mongo_uri)[settings.mongo_db]
    user_a = str(ObjectId())
    user_b = str(ObjectId())
    conversation_id = ObjectId()
    await db[COL_CONVERSATIONS].insert_one(
        _conversation(conversation_id=conversation_id, user_a=user_a, user_b=user_b)
    )
    await db[COL_MESSAGES].insert_one(
        _message(
            conversation_id=str(conversation_id),
            sender_id=user_a,
            receiver_id=user_b,
            text="dry run",
        )
    )

    stats = await run_cleanup(db, apply=False)

    assert stats.messages_scanned == 1
    assert stats.messages_eligible == 1
    assert stats.messages_cleaned == 1
    assert stats.fields_unset == {"text": 1, "media": 1, "call": 1, "status": 1}
    message = await db[COL_MESSAGES].find_one({})
    assert "text" in message
    assert "media" in message
    assert "call" in message


@pytest.mark.asyncio
async def test_cleanup_unsets_eligible_legacy_fields_and_is_idempotent(app_lifecycle):
    db = AsyncMongoClient(settings.mongo_uri)[settings.mongo_db]
    user_a = str(ObjectId())
    user_b = str(ObjectId())
    conversation_id = ObjectId()
    await db[COL_CONVERSATIONS].insert_one(
        _conversation(conversation_id=conversation_id, user_a=user_a, user_b=user_b)
    )
    await db[COL_MESSAGES].insert_one(
        _message(
            conversation_id=str(conversation_id),
            sender_id=user_a,
            receiver_id=user_b,
            text="clean me",
        )
    )

    first = await run_cleanup(db, apply=True)

    assert first.messages_cleaned == 1
    message = await db[COL_MESSAGES].find_one({})
    assert "text" not in message
    assert "media" not in message
    assert "call" not in message
    assert message["receiver_id"] == user_b
    assert "status" not in message
    assert message["content"]["plaintext"]["text"] == "clean me"

    second = await run_cleanup(db, apply=True)

    assert second.messages_cleaned == 0


@pytest.mark.asyncio
async def test_cleanup_unsets_call_field_after_call_content_repair(app_lifecycle):
    db = AsyncMongoClient(settings.mongo_uri)[settings.mongo_db]
    await _drop_legacy_call_index(db)
    user_a = str(ObjectId())
    user_b = str(ObjectId())
    conversation_id = ObjectId()
    await db[COL_CONVERSATIONS].insert_one(
        _conversation(conversation_id=conversation_id, user_a=user_a, user_b=user_b)
    )
    await db[COL_MESSAGES].insert_many(
        [
            _message(
                conversation_id=str(conversation_id),
                sender_id=user_a,
                receiver_id=user_b,
                text="first call",
                message_type="call",
                call_id="call-1",
            ),
            _message(
                conversation_id=str(conversation_id),
                sender_id=user_a,
                receiver_id=user_b,
                text="second call",
                message_type="call",
                call_id="call-2",
            ),
        ]
    )

    stats = await run_cleanup(db, apply=True)

    assert stats.messages_cleaned == 2
    assert stats.fields_unset == {"text": 2, "media": 2, "call": 2, "status": 2}
    messages = await db[COL_MESSAGES].find({}).to_list(length=10)
    assert all("call" not in message for message in messages)
    assert all("text" not in message for message in messages)
    assert all("media" not in message for message in messages)


@pytest.mark.asyncio
async def test_cleanup_does_not_restore_partially_cleaned_call_message(app_lifecycle):
    db = AsyncMongoClient(settings.mongo_uri)[settings.mongo_db]
    user_a = str(ObjectId())
    user_b = str(ObjectId())
    conversation_id = ObjectId()
    await db[COL_CONVERSATIONS].insert_one(
        _conversation(conversation_id=conversation_id, user_a=user_a, user_b=user_b)
    )
    message = _message(
        conversation_id=str(conversation_id),
        sender_id=user_a,
        receiver_id=user_b,
        text="partially cleaned call",
        message_type="call",
        call_id="call-1",
    )
    del message["call"]
    await db[COL_MESSAGES].insert_one(message)

    stats = await run_cleanup(db, apply=True)

    assert stats.call_fields_restored == 0
    restored = await db[COL_MESSAGES].find_one({})
    assert "call" not in restored
    assert "status" not in restored


@pytest.mark.asyncio
async def test_cleanup_skips_call_message_when_nested_call_id_missing(app_lifecycle):
    db = AsyncMongoClient(settings.mongo_uri)[settings.mongo_db]
    await _drop_legacy_call_index(db)
    user_a = str(ObjectId())
    user_b = str(ObjectId())
    conversation_id = ObjectId()
    await db[COL_CONVERSATIONS].insert_one(
        _conversation(conversation_id=conversation_id, user_a=user_a, user_b=user_b)
    )
    message = _message(
        conversation_id=str(conversation_id),
        sender_id=user_a,
        receiver_id=user_b,
        text="invalid nested call",
        message_type="call",
        call_id="call-1",
    )
    message["content"]["plaintext"]["call"] = None
    await db[COL_MESSAGES].insert_one(message)

    stats = await run_cleanup(db, apply=True)

    assert stats.messages_cleaned == 0
    assert stats.skipped_invalid_call_content == 1
    assert stats.skipped_ids == [str(message["_id"])]
    stored = await db[COL_MESSAGES].find_one({})
    assert stored["call"]["call_id"] == "call-1"
    assert stored["text"] == "invalid nested call"


@pytest.mark.asyncio
async def test_cleanup_skips_rows_without_content_or_canonical_conversation(
    app_lifecycle,
):
    db = AsyncMongoClient(settings.mongo_uri)[settings.mongo_db]
    user_a = str(ObjectId())
    user_b = str(ObjectId())
    await db[COL_MESSAGES].insert_many(
        [
            _message(
                conversation_id=f"{user_a}_{user_b}",
                sender_id=user_a,
                receiver_id=user_b,
                text="legacy key",
            ),
            _message(
                conversation_id=str(ObjectId()),
                sender_id=user_a,
                receiver_id=user_b,
                text="missing conversation",
            ),
            _message(
                conversation_id=str(ObjectId()),
                sender_id=user_a,
                receiver_id=user_b,
                text="missing content",
                include_content=False,
            ),
        ]
    )

    stats = await run_cleanup(db, apply=True)

    assert stats.messages_cleaned == 0
    assert stats.skipped_missing_content == 1
    assert stats.skipped_legacy_or_invalid_conversation_id == 1
    assert stats.skipped_missing_conversation == 1
    assert await db[COL_MESSAGES].count_documents({"text": {"$exists": True}}) == 3
