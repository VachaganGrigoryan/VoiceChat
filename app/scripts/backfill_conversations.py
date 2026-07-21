"""Backfill first-class conversation + participant docs from existing messages.

Historical messages store ``conversation_id`` as the derived DM key
(``sorted("{a}_{b}")``). This script materializes, for each distinct key:

- one ``ConversationDocument`` (``type="dm"``, ``dm_key`` = that key), and
- two ``ParticipantDocument`` rows with a best-effort ``last_read_at`` derived
  from message status,

and (optionally) wraps each message body into the ``content`` envelope
(``encryption="none"``) so the DB is canonical. Messages themselves keep their
``conversation_id`` (the DM key stays the entity's natural join key), so no
message-id rewriting happens and un-backfilled docs still deserialize.

Safe by default: runs as a DRY RUN unless ``--apply`` is passed. Idempotent:
existing conversations/participants are left untouched and only missing
``content`` is filled.
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from pymongo import AsyncMongoClient

from app.core.config import settings
from app.db.collections import (
    COL_CONVERSATION_PARTICIPANTS,
    COL_CONVERSATIONS,
    COL_MESSAGES,
)


@dataclass
class BackfillStats:
    conversations_scanned: int = 0
    conversations_created: int = 0
    conversations_skipped_existing: int = 0
    conversations_skipped_non_dm: int = 0
    participants_created: int = 0
    messages_content_wrapped: int = 0
    non_dm_keys: list[str] = field(default_factory=list)


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


async def _last_read_at_for(
    col_messages: Any, *, conversation_id: str, user_id: str
) -> datetime | None:
    """Best-effort read cursor: latest message the user sent or has read."""
    cursor = (
        col_messages.find(
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
    )
    docs = await cursor.to_list(length=1)
    return docs[0]["created_at"] if docs else None


async def run_backfill(
    db: Any, *, apply: bool, wrap_content: bool = True
) -> BackfillStats:
    stats = BackfillStats()
    col_messages = db[COL_MESSAGES]
    col_conversations = db[COL_CONVERSATIONS]
    col_participants = db[COL_CONVERSATION_PARTICIPANTS]

    conversation_ids: list[str] = await col_messages.distinct("conversation_id")

    for dm_key in conversation_ids:
        if not dm_key:
            continue
        stats.conversations_scanned += 1

        participants = await _participants_for(col_messages, dm_key)
        if len(participants) != 2:
            stats.conversations_skipped_non_dm += 1
            stats.non_dm_keys.append(dm_key)
            continue

        existing = await col_conversations.find_one({"type": "dm", "dm_key": dm_key})
        if existing is not None:
            conversation_id = str(existing["_id"])
            stats.conversations_skipped_existing += 1
        else:
            earliest = (
                await col_messages.find({"conversation_id": dm_key}, {"sender_id": 1})
                .sort("created_at", 1)
                .limit(1)
                .to_list(length=1)
            )
            created_by = (
                str(earliest[0]["sender_id"])
                if earliest and earliest[0].get("sender_id")
                else sorted(participants)[0]
            )
            latest = (
                await col_messages.find(
                    {"conversation_id": dm_key, "thread_root_id": None}
                )
                .sort("created_at", -1)
                .limit(1)
                .to_list(length=1)
            )
            preview = _preview_from_message(latest[0]) if latest else None
            last_message_at = latest[0]["created_at"] if latest else None
            now = datetime.now(UTC)
            doc = {
                "type": "dm",
                "participant_ids": sorted(participants),
                "created_by": created_by,
                "title": None,
                "encryption": "none",
                "dm_key": dm_key,
                "last_message_at": last_message_at,
                "last_message_preview": preview,
                "created_at": now,
                "updated_at": now,
            }
            if apply:
                res = await col_conversations.insert_one(doc)
                conversation_id = str(res.inserted_id)
            else:
                conversation_id = "(dry-run)"
            stats.conversations_created += 1

        for user_id in sorted(participants):
            if conversation_id != "(dry-run)":
                already = await col_participants.find_one(
                    {"conversation_id": conversation_id, "user_id": user_id}
                )
                if already is not None:
                    continue
            last_read_at = await _last_read_at_for(
                col_messages, conversation_id=dm_key, user_id=user_id
            )
            now = datetime.now(UTC)
            participant_doc = {
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
            if apply:
                await col_participants.insert_one(participant_doc)
            stats.participants_created += 1

    if wrap_content:
        async for msg in col_messages.find({"content": {"$exists": False}}):
            if apply:
                await col_messages.update_one(
                    {"_id": msg["_id"]},
                    {"$set": {"content": _build_content(msg)}},
                )
            stats.messages_content_wrapped += 1

    return stats


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually write changes. Without this flag the script is a dry run.",
    )
    parser.add_argument(
        "--no-wrap-content",
        action="store_true",
        help="Skip wrapping legacy message bodies into the content envelope.",
    )
    parser.add_argument("--mongo-uri", default=settings.mongo_uri)
    parser.add_argument("--mongo-db", default=settings.mongo_db)
    args = parser.parse_args()

    client: AsyncMongoClient = AsyncMongoClient(args.mongo_uri)
    db = client[args.mongo_db]
    try:
        stats = await run_backfill(
            db, apply=args.apply, wrap_content=not args.no_wrap_content
        )
    finally:
        await client.close()

    mode = "APPLIED" if args.apply else "DRY RUN (no writes)"
    print(f"[backfill_conversations] {mode} against {args.mongo_db}")
    print(f"  conversations scanned:          {stats.conversations_scanned}")
    print(f"  conversations created:          {stats.conversations_created}")
    print(f"  conversations skipped existing: {stats.conversations_skipped_existing}")
    print(f"  conversations skipped non-dm:   {stats.conversations_skipped_non_dm}")
    print(f"  participants created:           {stats.participants_created}")
    print(f"  messages content-wrapped:       {stats.messages_content_wrapped}")
    if stats.non_dm_keys:
        print(f"  non-dm keys (needs review): {stats.non_dm_keys[:10]}")


if __name__ == "__main__":
    asyncio.run(main())
