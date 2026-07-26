from __future__ import annotations

from datetime import UTC, datetime

import pytest
from bson import ObjectId
from pymongo import AsyncMongoClient

from app.core.config import settings
from app.db.collections import (
    COL_CHANNELS,
    COL_CONVERSATIONS,
    COL_MESSAGES,
    COL_RELATIONSHIPS,
    COL_USERS,
)
from app.scripts.migrate_channels_and_profile_feed import run_migration

NOW = datetime(2026, 7, 25, 12, 0, 0, tzinfo=UTC)


async def _seed(db) -> dict[str, str]:
    owner_id = ObjectId()
    existing_user_id = ObjectId()
    channel_id = ObjectId()
    root_id = ObjectId()
    reply_id = ObjectId()

    await db[COL_USERS].insert_many(
        [
            {
                "_id": owner_id,
                "email": "migration-owner@test.com",
                "username": "migration_owner",
                "is_private": False,
                "main_channel_id": str(channel_id),
                "created_at": NOW,
                "updated_at": NOW,
            },
            {
                "_id": existing_user_id,
                "email": "migration-existing@test.com",
                "username": "migration_existing",
                "is_private": True,
                "main_channel_id": None,
                "created_at": NOW,
                "updated_at": NOW,
            },
        ]
    )
    await db[COL_CONVERSATIONS].insert_one(
        {
            "_id": channel_id,
            "type": "channel",
            "created_by": str(owner_id),
            "title": "Owner feed",
            "slug": "owner-feed",
            "visibility": "public",
            "read_policy": "public",
            "posting_policy": "admins",
            "settings": {"comment_policy": "everyone"},
            "created_at": NOW,
            "updated_at": NOW,
        }
    )
    await db[COL_MESSAGES].insert_many(
        [
            {
                "_id": root_id,
                "conversation_id": str(channel_id),
                "container_type": "conversation",
                "container_id": str(channel_id),
                "sender_id": str(owner_id),
                "type": "text",
                "thread_root_id": None,
                "created_at": NOW,
                "updated_at": NOW,
            },
            {
                "_id": reply_id,
                "conversation_id": str(channel_id),
                "container_type": "conversation",
                "container_id": str(channel_id),
                "sender_id": str(existing_user_id),
                "type": "text",
                "thread_root_id": str(root_id),
                "created_at": NOW,
                "updated_at": NOW,
            },
        ]
    )
    await db[COL_RELATIONSHIPS].insert_one(
        {
            "kind": "follow",
            "user_id": str(existing_user_id),
            "target_type": "user",
            "target_id": str(owner_id),
            "status": "active",
            "initiation": "request",
            "initiated_by": str(existing_user_id),
            "created_at": NOW,
            "updated_at": NOW,
        }
    )
    return {
        "owner_id": str(owner_id),
        "existing_user_id": str(existing_user_id),
        "channel_id": str(channel_id),
    }


@pytest.mark.asyncio
async def test_channel_migration_dry_run_has_no_writes(app_lifecycle):
    db = AsyncMongoClient(settings.mongo_uri)[settings.mongo_db]
    ids = await _seed(db)

    stats = await run_migration(db, apply=False)

    assert stats.legacy_conversations_scanned == 1
    assert stats.channels_planned == 1
    assert stats.profile_channels_synthesized == 1
    assert stats.messages_readdressed == 2
    assert await db[COL_CHANNELS].count_documents({}) == 0
    assert (
        await db[COL_CONVERSATIONS].count_documents(
            {"_id": ObjectId(ids["channel_id"])}
        )
        == 1
    )


@pytest.mark.asyncio
async def test_channel_migration_preserves_ids_slugs_and_container_mapping(
    app_lifecycle,
):
    db = AsyncMongoClient(settings.mongo_uri)[settings.mongo_db]
    ids = await _seed(db)

    stats = await run_migration(db, apply=True)

    assert stats.conflicts == []
    channel = await db[COL_CHANNELS].find_one({"_id": ObjectId(ids["channel_id"])})
    assert channel is not None
    assert str(channel["_id"]) == ids["channel_id"]
    assert channel["legacy_conversation_id"] == ids["channel_id"]
    assert channel["slug"] == "owner-feed"
    assert channel["kind"] == "profile"
    assert channel["posting_policy"] == "moderators"
    assert channel["message_count"] == 2
    assert channel["follower_count"] == 1

    messages = (
        await db[COL_MESSAGES].find({"container_id": ids["channel_id"]}).to_list()
    )
    assert len(messages) == 2
    assert {message["container_type"] for message in messages} == {"channel"}

    owner = await db[COL_USERS].find_one({"_id": ObjectId(ids["owner_id"])})
    existing = await db[COL_USERS].find_one({"_id": ObjectId(ids["existing_user_id"])})
    assert owner["main_channel_id"] == ids["channel_id"]
    assert existing["main_channel_id"] is not None
    synthesized = await db[COL_CHANNELS].find_one(
        {"_id": ObjectId(existing["main_channel_id"])}
    )
    assert synthesized["kind"] == "profile"
    assert synthesized["visibility"] == "members"

    assert await db[COL_CONVERSATIONS].count_documents({"type": "channel"}) == 0

    await run_migration(db, apply=True)
    assert (
        await db[COL_CHANNELS].count_documents(
            {"owner.type": "user", "kind": "profile"}
        )
        == 2
    )
