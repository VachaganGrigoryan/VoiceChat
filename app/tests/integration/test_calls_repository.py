from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

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
