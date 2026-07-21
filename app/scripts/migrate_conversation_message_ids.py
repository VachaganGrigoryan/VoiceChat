"""Migrate legacy DM-key messages to first-class conversation ids.

Historical DM messages may store ``conversation_id`` as the derived sorted
``"{user_a}_{user_b}"`` key. The conversation cutover requires messages to point
at the real ``conversations._id`` instead. This script is safe by default: it is a
dry run unless ``--apply`` is passed, and it is idempotent.
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from bson import ObjectId
from pymongo import AsyncMongoClient

from app.core.config import settings
from app.db.collections import (
    COL_CONVERSATION_PARTICIPANTS,
    COL_CONVERSATIONS,
    COL_MESSAGES,
)


@dataclass
class ConversationIdMigrationStats:
    conversation_keys_scanned: int = 0
    conversations_created: int = 0
    conversations_existing: int = 0
    conversations_already_canonical: int = 0
    conversations_skipped_non_dm: int = 0
    participants_created: int = 0
    messages_rewritten: int = 0
    messages_content_wrapped: int = 0
    skipped_keys: list[str] = field(default_factory=list)
    key_mappings: dict[str, str] = field(default_factory=dict)


def _is_object_id(value: str) -> bool:
    return ObjectId.is_valid(value)


def _build_content(message: dict[str, Any]) -> dict[str, Any]:
    return {
        "encryption": "none",
        "type": message.get("type", "text"),
        "plaintext": {
            "text": message.get("text"),
            "media": message.get("media"),
            "call": message.get("call"),
        },
        "ciphertext": None,
        "envelope": None,
    }


def _preview_from_message(message: dict[str, Any]) -> dict[str, Any]:
    return {
        "message_id": str(message["_id"]),
        "sender_id": str(message.get("sender_id", "")),
        "type": message.get("type", "text"),
        "text": message.get("text"),
        "created_at": message.get("created_at"),
    }


async def _participants_for(col_messages: Any, conversation_id: str) -> set[str]:
    users: set[str] = set()
    async for msg in col_messages.find(
        {"conversation_id": conversation_id},
        {"sender_id": 1, "receiver_id": 1},
    ):
        if msg.get("sender_id"):
            users.add(str(msg["sender_id"]))
        if msg.get("receiver_id"):
            users.add(str(msg["receiver_id"]))
    return users


async def _latest_main_message(
    col_messages: Any, conversation_id: str
) -> dict[str, Any] | None:
    docs = (
        await col_messages.find({"conversation_id": conversation_id, "thread_root_id": None})
        .sort("created_at", -1)
        .limit(1)
        .to_list(length=1)
    )
    return docs[0] if docs else None


async def _earliest_message_sender(col_messages: Any, conversation_id: str) -> str | None:
    docs = (
        await col_messages.find({"conversation_id": conversation_id}, {"sender_id": 1})
        .sort("created_at", 1)
        .limit(1)
        .to_list(length=1)
    )
    if not docs or not docs[0].get("sender_id"):
        return None
    return str(docs[0]["sender_id"])


async def _last_read_at_for(
    col_messages: Any, *, conversation_id: str, user_id: str
) -> datetime | None:
    docs = (
        await col_messages.find(
            {
                "conversation_id": conversation_id,
                "$or": [
                    {"sender_id": user_id},
                    {"receiver_id": user_id, "status": "read"},
                ],
            },
            {"created_at": 1},
        )
        .sort("created_at", -1)
        .limit(1)
        .to_list(length=1)
    )
    return docs[0]["created_at"] if docs else None


async def _ensure_conversation(
    *,
    col_conversations: Any,
    col_messages: Any,
    dm_key: str,
    participants: set[str],
    apply: bool,
    stats: ConversationIdMigrationStats,
) -> str:
    existing = await col_conversations.find_one({"type": "dm", "dm_key": dm_key})
    if existing is not None:
        stats.conversations_existing += 1
        return str(existing["_id"])

    latest = await _latest_main_message(col_messages, dm_key)
    earliest_sender = await _earliest_message_sender(col_messages, dm_key)
    now = datetime.now(UTC)
    doc = {
        "type": "dm",
        "participant_ids": sorted(participants),
        "created_by": earliest_sender or sorted(participants)[0],
        "title": None,
        "encryption": "none",
        "dm_key": dm_key,
        "last_message_at": latest.get("created_at") if latest else None,
        "last_message_preview": _preview_from_message(latest) if latest else None,
        "created_at": now,
        "updated_at": now,
    }
    stats.conversations_created += 1
    if not apply:
        return "(dry-run)"

    result = await col_conversations.insert_one(doc)
    return str(result.inserted_id)


async def _ensure_participants(
    *,
    col_participants: Any,
    col_messages: Any,
    conversation_id: str,
    legacy_conversation_id: str,
    participants: set[str],
    apply: bool,
    stats: ConversationIdMigrationStats,
) -> None:
    for user_id in sorted(participants):
        if conversation_id != "(dry-run)":
            existing = await col_participants.find_one(
                {"conversation_id": conversation_id, "user_id": user_id}
            )
            if existing is not None:
                continue

        last_read_at = await _last_read_at_for(
            col_messages, conversation_id=legacy_conversation_id, user_id=user_id
        )
        now = datetime.now(UTC)
        doc = {
            "conversation_id": conversation_id,
            "user_id": user_id,
            "role": "owner" if user_id == sorted(participants)[0] else "member",
            "joined_at": now,
            "last_read_at": last_read_at,
            "last_read_message_id": None,
            "muted": False,
            "hidden": False,
            "created_at": now,
            "updated_at": now,
        }
        stats.participants_created += 1
        if apply:
            await col_participants.insert_one(doc)


async def _wrap_missing_content(
    *, col_messages: Any, conversation_id: str, apply: bool
) -> int:
    count = 0
    async for message in col_messages.find(
        {"conversation_id": conversation_id, "content": {"$exists": False}}
    ):
        count += 1
        if apply:
            await col_messages.update_one(
                {"_id": message["_id"]},
                {"$set": {"content": _build_content(message)}},
            )
    return count


async def run_migration(db: Any, *, apply: bool) -> ConversationIdMigrationStats:
    stats = ConversationIdMigrationStats()
    col_messages = db[COL_MESSAGES]
    col_conversations = db[COL_CONVERSATIONS]
    col_participants = db[COL_CONVERSATION_PARTICIPANTS]

    conversation_ids: list[str] = await col_messages.distinct("conversation_id")
    for raw_key in conversation_ids:
        if not raw_key:
            continue

        conversation_id = str(raw_key)
        stats.conversation_keys_scanned += 1

        if _is_object_id(conversation_id):
            existing = await col_conversations.find_one({"_id": ObjectId(conversation_id)})
            if existing is not None:
                stats.conversations_already_canonical += 1
                stats.messages_content_wrapped += await _wrap_missing_content(
                    col_messages=col_messages,
                    conversation_id=conversation_id,
                    apply=apply,
                )
                continue

        participants = await _participants_for(col_messages, conversation_id)
        if len(participants) != 2:
            stats.conversations_skipped_non_dm += 1
            stats.skipped_keys.append(conversation_id)
            continue

        target_conversation_id = await _ensure_conversation(
            col_conversations=col_conversations,
            col_messages=col_messages,
            dm_key=conversation_id,
            participants=participants,
            apply=apply,
            stats=stats,
        )
        stats.key_mappings[conversation_id] = target_conversation_id

        await _ensure_participants(
            col_participants=col_participants,
            col_messages=col_messages,
            conversation_id=target_conversation_id,
            legacy_conversation_id=conversation_id,
            participants=participants,
            apply=apply,
            stats=stats,
        )

        stats.messages_content_wrapped += await _wrap_missing_content(
            col_messages=col_messages,
            conversation_id=conversation_id,
            apply=apply,
        )

        rewrite_count = await col_messages.count_documents(
            {"conversation_id": conversation_id}
        )
        stats.messages_rewritten += rewrite_count
        if apply:
            await col_messages.update_many(
                {"conversation_id": conversation_id},
                {"$set": {"conversation_id": target_conversation_id}},
            )

    return stats


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually write changes. Without this flag the script is a dry run.",
    )
    parser.add_argument("--mongo-uri", default=settings.mongo_uri)
    parser.add_argument("--mongo-db", default=settings.mongo_db)
    args = parser.parse_args()

    client: AsyncMongoClient = AsyncMongoClient(args.mongo_uri)
    db = client[args.mongo_db]
    try:
        stats = await run_migration(db, apply=args.apply)
    finally:
        await client.close()

    mode = "APPLIED" if args.apply else "DRY RUN (no writes)"
    print(f"[migrate_conversation_message_ids] {mode} against {args.mongo_db}")
    print(f"  conversation keys scanned:        {stats.conversation_keys_scanned}")
    print(f"  conversations created:            {stats.conversations_created}")
    print(f"  conversations existing:           {stats.conversations_existing}")
    print(f"  conversations already canonical:  {stats.conversations_already_canonical}")
    print(f"  conversations skipped non-dm:     {stats.conversations_skipped_non_dm}")
    print(f"  participants created:             {stats.participants_created}")
    print(f"  messages rewritten:               {stats.messages_rewritten}")
    print(f"  messages content-wrapped:         {stats.messages_content_wrapped}")
    if stats.skipped_keys:
        print(f"  skipped keys (needs review): {stats.skipped_keys[:10]}")
    if stats.key_mappings:
        print(f"  key mappings: {dict(list(stats.key_mappings.items())[:10])}")


if __name__ == "__main__":
    asyncio.run(main())
