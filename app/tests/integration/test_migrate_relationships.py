from __future__ import annotations

from datetime import UTC, datetime

import pytest
from bson import ObjectId
from pymongo import AsyncMongoClient

from app.core.config import settings
from app.db.collections import (
    COL_CONVERSATION_PARTICIPANTS,
    COL_JOIN_REQUESTS,
    COL_PINGS,
    COL_RELATIONSHIPS,
    COL_SPACE_MEMBERS,
)
from app.scripts.migrate_relationships import run_migration

NOW = datetime(2026, 4, 2, 9, 0, 0, tzinfo=UTC)


def _pair_id(user_a: str, user_b: str) -> str:
    return f"{user_a}_{user_b}" if user_a < user_b else f"{user_b}_{user_a}"


async def _seed(db) -> dict[str, str]:
    """Seed one row per legacy collection and return the ids they reference."""
    alice, bob = str(ObjectId()), str(ObjectId())
    conversation_id, channel_id = str(ObjectId()), str(ObjectId())
    space_id, other_space_id = str(ObjectId()), str(ObjectId())

    await db[COL_PINGS].insert_many(
        [
            {
                "pair_id": _pair_id(alice, bob),
                "from_user_id": alice,
                "to_user_id": bob,
                "status": "accepted",
                "responded_at": NOW,
                "created_at": NOW,
                "updated_at": NOW,
            },
            {
                "pair_id": _pair_id(alice, str(ObjectId())),
                "from_user_id": alice,
                "to_user_id": str(ObjectId()),
                "status": "blocked",
                "created_at": NOW,
                "updated_at": NOW,
            },
        ]
    )
    await db[COL_CONVERSATION_PARTICIPANTS].insert_many(
        [
            {
                "conversation_id": conversation_id,
                "user_id": alice,
                "role": "owner",
                "joined_at": NOW,
                "pinned": True,
                "folder": "Work",
                "last_read_message_id": "m1",
                "created_at": NOW,
                "updated_at": NOW,
            },
            {
                "conversation_id": channel_id,
                "user_id": bob,
                "role": "subscriber",
                "joined_at": NOW,
                "created_at": NOW,
                "updated_at": NOW,
            },
        ]
    )
    await db[COL_SPACE_MEMBERS].insert_one(
        {
            "space_id": space_id,
            "user_id": alice,
            "role": "admin",
            "joined_at": NOW,
            "created_at": NOW,
            "updated_at": NOW,
        }
    )
    await db[COL_JOIN_REQUESTS].insert_many(
        [
            {
                "target_type": "space",
                "target_id": other_space_id,
                "user_id": bob,
                "status": "pending",
                "created_at": NOW,
                "updated_at": NOW,
            },
            # Already an active member — must not overwrite the membership.
            {
                "target_type": "space",
                "target_id": space_id,
                "user_id": alice,
                "status": "approved",
                "responded_at": NOW,
                "created_at": NOW,
                "updated_at": NOW,
            },
        ]
    )
    return {
        "alice": alice,
        "bob": bob,
        "conversation_id": conversation_id,
        "channel_id": channel_id,
        "space_id": space_id,
        "other_space_id": other_space_id,
    }


@pytest.mark.asyncio
async def test_relationship_migration_dry_run_reports_without_writes(app_lifecycle):
    db = AsyncMongoClient(settings.mongo_uri)[settings.mongo_db]
    await _seed(db)

    stats = await run_migration(db, apply=False)

    assert stats.pings_scanned == 2
    assert stats.connections_planned == 1
    assert stats.skipped_blocked_pings == 1
    assert stats.participants_scanned == 2
    assert stats.conversation_memberships_planned == 1
    assert stats.channel_follows_planned == 1
    assert stats.space_members_scanned == 1
    assert stats.space_memberships_planned == 1
    assert stats.join_requests_scanned == 2
    # The approved request duplicates an existing membership and is dropped.
    assert stats.join_request_memberships_planned == 1
    assert await db[COL_RELATIONSHIPS].count_documents({}) == 0


@pytest.mark.asyncio
async def test_relationship_migration_counts_match_legacy_collections(app_lifecycle):
    db = AsyncMongoClient(settings.mongo_uri)[settings.mongo_db]
    ids = await _seed(db)

    stats = await run_migration(db, apply=True)

    assert stats.relationships_written == 5

    # 1. pings -> connection, pair_id preserved, accepted -> active
    connection = await db[COL_RELATIONSHIPS].find_one({"kind": "connection"})
    assert connection["pair_id"] == _pair_id(ids["alice"], ids["bob"])
    assert connection["status"] == "active"
    assert connection["approved_by"] == ids["bob"]
    # Mongo returns naive UTC datetimes for the round-tripped timestamps.
    assert connection["activated_at"] == NOW.replace(tzinfo=None)
    # A blocked pair is represented by `blocks`, never by a connection.
    assert await db[COL_RELATIONSHIPS].count_documents({"kind": "connection"}) == 1

    # 2. participants -> conversation membership carrying inbox state
    membership = await db[COL_RELATIONSHIPS].find_one(
        {"kind": "membership", "target_type": "conversation"}
    )
    assert membership["user_id"] == ids["alice"]
    assert membership["target_id"] == ids["conversation_id"]
    assert membership["status"] == "active"
    assert membership["role_ids"] == ["owner"]
    assert membership["state"]["pinned"] is True
    assert membership["state"]["folder"] == "Work"
    assert membership["state"]["last_read_message_id"] == "m1"

    #    subscriber -> channel follow, not a membership
    follow = await db[COL_RELATIONSHIPS].find_one({"kind": "follow"})
    assert follow["target_type"] == "channel"
    assert follow["target_id"] == ids["channel_id"]
    assert follow["user_id"] == ids["bob"]
    assert follow["status"] == "active"

    # 3. space_members -> space membership
    space_membership = await db[COL_RELATIONSHIPS].find_one(
        {
            "kind": "membership",
            "target_type": "space",
            "target_id": ids["space_id"],
        }
    )
    assert space_membership["user_id"] == ids["alice"]
    assert space_membership["status"] == "active"
    assert space_membership["role_ids"] == ["admin"]

    # 4. join_requests -> pending membership with matching initiation
    join_membership = await db[COL_RELATIONSHIPS].find_one(
        {
            "kind": "membership",
            "target_type": "space",
            "target_id": ids["other_space_id"],
        }
    )
    assert join_membership["user_id"] == ids["bob"]
    assert join_membership["status"] == "pending"
    assert join_membership["initiation"] == "request"

    # Totals line up with the legacy rows that map to a relationship.
    legacy_total = (
        await db[COL_PINGS].count_documents({"status": {"$ne": "blocked"}})
        + await db[COL_CONVERSATION_PARTICIPANTS].count_documents({})
        + await db[COL_SPACE_MEMBERS].count_documents({})
        + await db[COL_JOIN_REQUESTS].count_documents({"status": "pending"})
    )
    assert await db[COL_RELATIONSHIPS].count_documents({}) == legacy_total


@pytest.mark.asyncio
async def test_relationship_migration_is_idempotent_and_leaves_legacy_readable(
    app_lifecycle,
):
    db = AsyncMongoClient(settings.mongo_uri)[settings.mongo_db]
    await _seed(db)

    await run_migration(db, apply=True)
    first_count = await db[COL_RELATIONSHIPS].count_documents({})
    await run_migration(db, apply=True)

    assert await db[COL_RELATIONSHIPS].count_documents({}) == first_count
    # Legacy collections stay intact so the cutover can be verified/rolled back.
    assert await db[COL_PINGS].count_documents({}) == 2
    assert await db[COL_CONVERSATION_PARTICIPANTS].count_documents({}) == 2
    assert await db[COL_SPACE_MEMBERS].count_documents({}) == 1
    assert await db[COL_JOIN_REQUESTS].count_documents({}) == 2
