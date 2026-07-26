"""Move legacy channel conversations into first-class channels.

One-shot and dry-run by default. Take a database snapshot before applying:

  poetry run python -m app.scripts.migrate_channels_and_profile_feed
  poetry run python -m app.scripts.migrate_channels_and_profile_feed --apply
  docker compose exec api python -m app.scripts.migrate_channels_and_profile_feed
  docker compose exec api python -m app.scripts.migrate_channels_and_profile_feed --apply

The migration preserves each legacy conversation ``_id`` and slug, readdresses
its messages to ``container_type="channel"`` with the same ``container_id``,
creates missing profile channels, and removes the migrated conversation rows.
Rollback is restoring the snapshot taken immediately before ``--apply``.
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

from bson import ObjectId
from pymongo import AsyncMongoClient
from pymongo.errors import DuplicateKeyError

from app.core.config import settings
from app.db.collections import (
    COL_CHANNELS,
    COL_CONVERSATIONS,
    COL_MESSAGES,
    COL_RELATIONSHIPS,
    COL_USERS,
)


@dataclass
class ChannelMigrationStats:
    legacy_conversations_scanned: int = 0
    channels_planned: int = 0
    channels_written: int = 0
    profile_channels_reused: int = 0
    profile_channels_synthesized: int = 0
    users_repointed: int = 0
    messages_scanned: int = 0
    messages_readdressed: int = 0
    conversations_deleted: int = 0
    conflicts: list[str] = field(default_factory=list)


def _read_visibility(conversation: dict[str, Any]) -> str:
    read_policy = conversation.get("read_policy")
    if read_policy == "public":
        return "public"
    if read_policy in {"members", "contacts"}:
        return "members"
    return "public" if conversation.get("visibility") == "public" else "private"


def _posting_policy(conversation: dict[str, Any]) -> str:
    posting_policy = conversation.get("posting_policy", "everyone")
    return "moderators" if posting_policy == "admins" else posting_policy


def _owner_for(
    conversation: dict[str, Any], *, profile_user: dict[str, Any] | None
) -> dict[str, str]:
    if profile_user is not None:
        return {"type": "user", "id": str(profile_user["_id"])}
    owner = conversation.get("owner")
    if isinstance(owner, dict) and owner.get("type") in {"user", "space"}:
        return {"type": owner["type"], "id": str(owner["id"])}
    if conversation.get("space_id") is not None:
        return {"type": "space", "id": str(conversation["space_id"])}
    return {"type": "user", "id": str(conversation["created_by"])}


async def _channel_counters(
    db: Any,
    *,
    channel_id: str,
    profile_owner_id: str | None,
) -> tuple[int, int, dict[str, Any] | None]:
    message_query = {
        "$or": [
            {"container_type": "conversation", "container_id": channel_id},
            {"conversation_id": channel_id},
        ]
    }
    message_count = await db[COL_MESSAGES].count_documents(message_query)
    last_message = await db[COL_MESSAGES].find_one(
        message_query,
        {"_id": 1, "created_at": 1},
        sort=[("created_at", -1), ("_id", -1)],
    )

    follow_targets: list[dict[str, str]] = [
        {"target_type": "channel", "target_id": channel_id}
    ]
    if profile_owner_id is not None:
        follow_targets.append({"target_type": "user", "target_id": profile_owner_id})
    follower_count = await db[COL_RELATIONSHIPS].count_documents(
        {"kind": "follow", "status": "active", "$or": follow_targets}
    )
    return message_count, follower_count, last_message


async def migrate_legacy_channels(
    db: Any, stats: ChannelMigrationStats, *, apply: bool
) -> set[str]:
    users_by_main: dict[str, dict[str, Any]] = {}
    async for user in db[COL_USERS].find(
        {"main_channel_id": {"$nin": [None, ""]}},
        {"username": 1, "main_channel_id": 1, "is_private": 1},
    ):
        users_by_main[str(user["main_channel_id"])] = user

    migrated_ids: set[str] = set()
    async for conversation in db[COL_CONVERSATIONS].find({"type": "channel"}):
        stats.legacy_conversations_scanned += 1
        channel_id = str(conversation["_id"])
        migrated_ids.add(channel_id)
        profile_user = users_by_main.get(channel_id)
        existing = await db[COL_CHANNELS].find_one({"_id": conversation["_id"]})
        if existing is not None:
            stats.profile_channels_reused += int(existing.get("kind") == "profile")
            continue

        owner = _owner_for(conversation, profile_user=profile_user)
        is_profile = profile_user is not None
        visibility = _read_visibility(conversation)
        if is_profile and profile_user.get("is_private"):
            visibility = "members"
        message_count, follower_count, last_message = await _channel_counters(
            db,
            channel_id=channel_id,
            profile_owner_id=owner["id"] if is_profile else None,
        )
        now = datetime.now(UTC)
        settings_data = conversation.get("settings") or {}
        kind = "profile" if is_profile else settings_data.get("channel_kind", "text")
        if kind not in {"profile", "text", "announcement"}:
            kind = "text"
        slug = conversation.get("slug") or (
            "feed" if is_profile else f"channel-{channel_id}"
        )
        name = conversation.get("title")
        if not name and is_profile:
            name = f"{profile_user.get('username', 'User')}'s feed"
        channel = {
            "_id": conversation["_id"],
            "owner": owner,
            "space_id": (
                str(conversation["space_id"])
                if conversation.get("space_id") is not None
                else None
            ),
            "kind": kind,
            "slug": slug,
            "name": name or "Channel",
            "description": conversation.get("description"),
            "avatar": conversation.get("image"),
            "banner": settings_data.get("banner"),
            "visibility": visibility,
            "join_policy": settings_data.get(
                "join_policy",
                "open"
                if visibility == "public"
                else "approval"
                if visibility == "members"
                else "invite_only",
            ),
            "posting_policy": _posting_policy(conversation),
            "comment_policy": conversation.get(
                "comment_policy", settings_data.get("comment_policy", "everyone")
            ),
            "tags": conversation.get("tags", settings_data.get("tags", [])),
            "message_count": message_count,
            "follower_count": follower_count,
            "last_message_id": (
                str(last_message["_id"]) if last_message is not None else None
            ),
            "last_activity_at": (
                last_message.get("created_at") if last_message is not None else None
            ),
            "legacy_conversation_id": channel_id,
            "created_by": str(conversation["created_by"]),
            "created_at": conversation.get("created_at", now),
            "updated_at": conversation.get("updated_at", now),
        }
        stats.channels_planned += 1
        if not apply:
            continue
        try:
            await db[COL_CHANNELS].insert_one(channel)
        except DuplicateKeyError:
            stats.conflicts.append(channel_id)
            continue
        stats.channels_written += 1
    return migrated_ids


async def synthesize_profile_channels(
    db: Any,
    stats: ChannelMigrationStats,
    *,
    apply: bool,
    migrated_ids: set[str],
) -> None:
    async for user in db[COL_USERS].find(
        {},
        {"username": 1, "is_private": 1, "main_channel_id": 1, "created_at": 1},
    ):
        user_id = str(user["_id"])
        main_channel_id = (
            str(user["main_channel_id"])
            if user.get("main_channel_id") is not None
            else None
        )
        profile = await db[COL_CHANNELS].find_one(
            {
                "owner.type": "user",
                "owner.id": user_id,
                "kind": "profile",
            }
        )
        if profile is not None:
            profile_id = str(profile["_id"])
            stats.profile_channels_reused += 1
        elif main_channel_id in migrated_ids:
            profile_id = main_channel_id
            stats.profile_channels_reused += 1
        else:
            profile_id = str(ObjectId())
            stats.profile_channels_synthesized += 1
            if apply:
                now = datetime.now(UTC)
                try:
                    await db[COL_CHANNELS].insert_one(
                        {
                            "_id": ObjectId(profile_id),
                            "owner": {"type": "user", "id": user_id},
                            "space_id": None,
                            "kind": "profile",
                            "slug": "feed",
                            "name": f"{user.get('username', 'User')}'s feed",
                            "description": None,
                            "avatar": None,
                            "banner": None,
                            "visibility": (
                                "members" if user.get("is_private") else "public"
                            ),
                            "join_policy": (
                                "approval" if user.get("is_private") else "open"
                            ),
                            "posting_policy": "owner",
                            "comment_policy": "everyone",
                            "tags": [],
                            "message_count": 0,
                            "follower_count": 0,
                            "last_message_id": None,
                            "last_activity_at": None,
                            "legacy_conversation_id": None,
                            "created_by": user_id,
                            "created_at": user.get("created_at", now),
                            "updated_at": now,
                        }
                    )
                except DuplicateKeyError:
                    winner = await db[COL_CHANNELS].find_one(
                        {
                            "owner.type": "user",
                            "owner.id": user_id,
                            "kind": "profile",
                        }
                    )
                    if winner is None:
                        stats.conflicts.append(f"profile:{user_id}")
                        continue
                    profile_id = str(winner["_id"])

        if main_channel_id == profile_id:
            continue
        stats.users_repointed += 1
        if apply:
            await db[COL_USERS].update_one(
                {"_id": user["_id"]},
                {
                    "$set": {
                        "main_channel_id": profile_id,
                        "updated_at": datetime.now(UTC),
                    }
                },
            )


async def readdress_channel_messages(
    db: Any,
    stats: ChannelMigrationStats,
    *,
    apply: bool,
    migrated_ids: set[str],
) -> None:
    for channel_id in migrated_ids:
        query = {
            "$or": [
                {"container_type": "conversation", "container_id": channel_id},
                {"conversation_id": channel_id},
            ]
        }
        matched = await db[COL_MESSAGES].count_documents(query)
        stats.messages_scanned += matched
        stats.messages_readdressed += matched
        if apply and matched:
            await db[COL_MESSAGES].update_many(
                query,
                {
                    "$set": {
                        "container_type": "channel",
                        "container_id": channel_id,
                        "updated_at": datetime.now(UTC),
                    }
                },
            )


async def remove_legacy_channels(
    db: Any,
    stats: ChannelMigrationStats,
    *,
    apply: bool,
    migrated_ids: set[str],
) -> None:
    if not apply:
        stats.conversations_deleted = len(migrated_ids)
        return
    if stats.conflicts:
        raise RuntimeError(
            "Channel conflicts must be resolved before legacy conversations are removed"
        )
    remaining = await db[COL_MESSAGES].count_documents(
        {
            "container_type": "conversation",
            "container_id": {"$in": list(migrated_ids)},
        }
    )
    if remaining:
        raise RuntimeError(
            f"{remaining} legacy channel messages remain; refusing to delete conversations"
        )
    object_ids = [ObjectId(channel_id) for channel_id in migrated_ids]
    result = await db[COL_CONVERSATIONS].delete_many(
        {"_id": {"$in": object_ids}, "type": "channel"}
    )
    stats.conversations_deleted = int(result.deleted_count)


async def run_migration(
    db: Any, *, apply: bool, keep_conversations: bool = False
) -> ChannelMigrationStats:
    stats = ChannelMigrationStats()
    migrated_ids = await migrate_legacy_channels(db, stats, apply=apply)
    await synthesize_profile_channels(
        db,
        stats,
        apply=apply,
        migrated_ids=migrated_ids,
    )
    await readdress_channel_messages(
        db,
        stats,
        apply=apply,
        migrated_ids=migrated_ids,
    )
    if not keep_conversations:
        await remove_legacy_channels(
            db,
            stats,
            apply=apply,
            migrated_ids=migrated_ids,
        )
    return stats


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write channels, repoint users/messages, and remove legacy channel rows.",
    )
    parser.add_argument(
        "--keep-conversations",
        action="store_true",
        help="Keep migrated conversation rows for a staged verification run.",
    )
    parser.add_argument("--mongo-uri", default=settings.mongo_uri)
    parser.add_argument("--mongo-db", default=settings.mongo_db)
    args = parser.parse_args()

    client: AsyncMongoClient = AsyncMongoClient(args.mongo_uri)
    db = client[args.mongo_db]
    try:
        stats = await run_migration(
            db,
            apply=args.apply,
            keep_conversations=args.keep_conversations,
        )
    finally:
        await client.close()

    mode = "APPLIED" if args.apply else "DRY RUN (no writes)"
    print(f"[migrate_channels_and_profile_feed] {mode} against {args.mongo_db}")
    for key, value in asdict(stats).items():
        print(f"  {key}: {value}")


if __name__ == "__main__":
    asyncio.run(main())
