from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from bson import ObjectId

from app.db.indexes import ensure_indexes
from app.db.mongo import get_db
from app.modules.calls.repository import CallsRepository


@pytest.mark.asyncio
async def test_expire_stale_calls_releases_live_call_uniqueness_lock():
    db = get_db()
    await ensure_indexes(db)
    repo = CallsRepository(db)

    call = await repo.create_call(
        caller_user_id="u1",
        callee_user_id="u2",
        call_type="audio",
        expires_at=datetime.now(UTC) - timedelta(seconds=5),
    )

    modified_count = await repo.expire_stale_calls()

    assert modified_count >= 1

    released = await repo.find_by_id(str(call["_id"]))
    assert released is not None
    assert released["status"] == "expired"
    assert released["is_live"] is False

    replacement = await repo.create_call(
        caller_user_id="u1",
        callee_user_id="u2",
        call_type="video",
        expires_at=datetime.now(UTC) + timedelta(seconds=30),
    )

    assert str(replacement["_id"]) != str(call["_id"])
    assert replacement["status"] == "ringing"


@pytest.mark.asyncio
async def test_reconnect_timeout_releases_live_call_uniqueness_lock():
    db = get_db()
    await ensure_indexes(db)
    repo = CallsRepository(db)

    call = await repo.create_call(
        caller_user_id="u1",
        callee_user_id="u2",
        call_type="audio",
        expires_at=datetime.now(UTC) + timedelta(seconds=30),
    )
    call_id = str(call["_id"])

    accepted = await repo.accept_call(call_id=call_id, callee_user_id="u2")
    assert accepted is not None
    connecting = await repo.set_connecting(call_id=call_id, caller_user_id="u1")
    assert connecting is not None
    active = await repo.set_active(call_id=call_id, participant_user_id="u1")
    assert active is not None

    reconnecting = await repo.mark_reconnecting(
        call_id=call_id,
        participant_user_id="u1",
        reconnect_deadline_at=datetime.now(UTC) - timedelta(seconds=1),
    )
    assert reconnecting is not None
    assert reconnecting["status"] == "reconnecting"

    modified_count = await repo.expire_stale_calls()

    assert modified_count >= 1

    released = await repo.find_by_id(call_id)
    assert released is not None
    assert released["status"] == "ended"
    assert released["is_live"] is False

    replacement = await repo.create_call(
        caller_user_id="u1",
        callee_user_id="u2",
        call_type="video",
        expires_at=datetime.now(UTC) + timedelta(seconds=30),
    )

    assert str(replacement["_id"]) != call_id


@pytest.mark.asyncio
async def test_list_history_returns_terminal_calls_with_peer_filter_and_cursor():
    db = get_db()
    await ensure_indexes(db)
    await db["calls"].delete_many({})
    repo = CallsRepository(db)

    base_time = datetime(2026, 4, 1, 12, 0, 0, tzinfo=UTC)
    first_id = ObjectId()
    second_id = ObjectId()
    third_id = ObjectId()

    await db["calls"].insert_many(
        [
            {
                "_id": first_id,
                "caller_user_id": "u1",
                "callee_user_id": "u2",
                "participant_user_ids": ["u1", "u2"],
                "type": "audio",
                "status": "ended",
                "room_id": f"call:{first_id}",
                "created_at": base_time - timedelta(minutes=5),
                "updated_at": base_time,
                "answered_at": base_time - timedelta(minutes=4),
                "ended_at": base_time,
                "expires_at": None,
                "reconnect_deadline_at": None,
                "disconnected_user_ids": [],
                "is_live": False,
                "history_message_id": "m1",
            },
            {
                "_id": second_id,
                "caller_user_id": "u2",
                "callee_user_id": "u1",
                "participant_user_ids": ["u2", "u1"],
                "type": "video",
                "status": "rejected",
                "room_id": f"call:{second_id}",
                "created_at": base_time - timedelta(minutes=2),
                "updated_at": base_time + timedelta(minutes=1),
                "answered_at": None,
                "ended_at": base_time + timedelta(minutes=1),
                "expires_at": None,
                "reconnect_deadline_at": None,
                "disconnected_user_ids": [],
                "is_live": False,
                "history_message_id": "m2",
            },
            {
                "_id": third_id,
                "caller_user_id": "u1",
                "callee_user_id": "u3",
                "participant_user_ids": ["u1", "u3"],
                "type": "audio",
                "status": "expired",
                "room_id": f"call:{third_id}",
                "created_at": base_time - timedelta(minutes=1),
                "updated_at": base_time + timedelta(minutes=2),
                "answered_at": None,
                "ended_at": base_time + timedelta(minutes=2),
                "expires_at": None,
                "reconnect_deadline_at": None,
                "disconnected_user_ids": [],
                "is_live": False,
                "history_message_id": None,
            },
        ]
    )

    items, next_cursor = await repo.list_history(user_id="u1", peer_user_id="u2", limit=1)

    assert len(items) == 1
    assert str(items[0]["_id"]) == str(second_id)
    assert next_cursor is not None

    older_items, older_cursor = await repo.list_history(
        user_id="u1",
        peer_user_id="u2",
        limit=1,
        cursor=next_cursor,
    )

    assert [str(item["_id"]) for item in older_items] == [str(first_id)]
    assert older_cursor is None

    all_items, _ = await repo.list_history(user_id="u1", limit=10)
    assert [str(item["_id"]) for item in all_items] == [
        str(third_id),
        str(second_id),
        str(first_id),
    ]
