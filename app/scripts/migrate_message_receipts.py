"""Backfill per-message receipts from legacy message status fields.

Dry-run by default. This must run after messages have real conversation ids and
before legacy ``receiver_id``/``status`` fields are removed.
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from bson import ObjectId
from pymongo import AsyncMongoClient, UpdateOne

from app.core.config import settings
from app.db.collections import (
    COL_CONVERSATION_PARTICIPANTS,
    COL_CONVERSATIONS,
    COL_MESSAGE_RECEIPTS,
    COL_MESSAGES,
)


@dataclass
class MessageReceiptMigrationStats:
    messages_scanned: int = 0
    receipts_planned: int = 0
    receipts_written: int = 0
    skipped_missing_conversation: int = 0
    skipped_missing_receiver: int = 0
    skipped_sender_receiver_same: int = 0
    participant_read_receipts_planned: int = 0
    participant_read_receipts_written: int = 0
    skipped_ids: list[str] = field(default_factory=list)


async def _conversation_exists(col_conversations: Any, conversation_id: str) -> bool:
    if not ObjectId.is_valid(conversation_id):
        return False
    return (
        await col_conversations.find_one({"_id": ObjectId(conversation_id)}, {"_id": 1})
        is not None
    )


def _receipt_update(
    *,
    message: dict[str, Any],
    user_id: str,
    delivered_at: datetime | None,
    read_at: datetime | None,
) -> UpdateOne:
    now = datetime.now(UTC)
    set_data: dict[str, Any] = {"updated_at": now}
    if delivered_at is not None or read_at is not None:
        set_data["delivered_at"] = delivered_at or read_at
    if read_at is not None:
        set_data["read_at"] = read_at

    return UpdateOne(
        {"message_id": str(message["_id"]), "user_id": user_id},
        {
            "$set": set_data,
            "$setOnInsert": {
                "conversation_id": str(message["conversation_id"]),
                "message_id": str(message["_id"]),
                "user_id": user_id,
                "created_at": now,
            },
        },
        upsert=True,
    )


async def run_migration(db: Any, *, apply: bool) -> MessageReceiptMigrationStats:
    stats = MessageReceiptMigrationStats()
    col_messages = db[COL_MESSAGES]
    col_conversations = db[COL_CONVERSATIONS]
    col_participants = db[COL_CONVERSATION_PARTICIPANTS]
    col_receipts = db[COL_MESSAGE_RECEIPTS]
    updates: list[UpdateOne] = []

    async for message in col_messages.find(
        {"receiver_id": {"$exists": True}},
        {
            "_id": 1,
            "conversation_id": 1,
            "sender_id": 1,
            "receiver_id": 1,
            "status": 1,
            "delivered_at": 1,
            "read_at": 1,
            "created_at": 1,
        },
    ):
        stats.messages_scanned += 1
        message_id = str(message["_id"])
        conversation_id = str(message.get("conversation_id") or "")
        if not await _conversation_exists(col_conversations, conversation_id):
            stats.skipped_missing_conversation += 1
            stats.skipped_ids.append(message_id)
            continue

        receiver_id = str(message.get("receiver_id") or "")
        if not receiver_id:
            stats.skipped_missing_receiver += 1
            stats.skipped_ids.append(message_id)
            continue
        if receiver_id == str(message.get("sender_id")):
            stats.skipped_sender_receiver_same += 1
            stats.skipped_ids.append(message_id)
            continue

        status = message.get("status")
        read_at = message.get("read_at") if status == "read" else None
        delivered_at = (
            message.get("delivered_at") if status in {"delivered", "read"} else None
        )
        if status == "read" and read_at is None:
            read_at = message.get("created_at")
        if status in {"delivered", "read"} and delivered_at is None:
            delivered_at = read_at or message.get("created_at")

        updates.append(
            _receipt_update(
                message=message,
                user_id=receiver_id,
                delivered_at=delivered_at,
                read_at=read_at,
            )
        )
        stats.receipts_planned += 1

    async for participant in col_participants.find(
        {"last_read_at": {"$ne": None}},
        {"conversation_id": 1, "user_id": 1, "last_read_at": 1},
    ):
        read_at = participant.get("last_read_at")
        if read_at is None:
            continue
        user_id = str(participant["user_id"])
        async for message in col_messages.find(
            {
                "conversation_id": str(participant["conversation_id"]),
                "sender_id": {"$ne": user_id},
                "created_at": {"$lte": read_at},
            },
            {"_id": 1, "conversation_id": 1, "sender_id": 1, "created_at": 1},
        ):
            updates.append(
                _receipt_update(
                    message=message,
                    user_id=user_id,
                    delivered_at=read_at,
                    read_at=read_at,
                )
            )
            stats.participant_read_receipts_planned += 1

    if apply and updates:
        result = await col_receipts.bulk_write(updates, ordered=False)
        stats.receipts_written = int(result.upserted_count + result.modified_count)
        stats.participant_read_receipts_written = stats.receipts_written

    return stats


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually write receipt rows. Without this flag the script is a dry run.",
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
    print(f"[migrate_message_receipts] {mode} against {args.mongo_db}")
    print(f"  messages scanned:                    {stats.messages_scanned}")
    print(f"  direct receipts planned:             {stats.receipts_planned}")
    print(f"  direct receipts written:             {stats.receipts_written}")
    print(
        "  participant read receipts planned:   "
        f"{stats.participant_read_receipts_planned}"
    )
    print(
        "  participant read receipts written:   "
        f"{stats.participant_read_receipts_written}"
    )
    print(
        f"  skipped missing conversation:        {stats.skipped_missing_conversation}"
    )
    print(f"  skipped missing receiver:            {stats.skipped_missing_receiver}")
    print(
        f"  skipped sender receiver same:        {stats.skipped_sender_receiver_same}"
    )
    if stats.skipped_ids:
        print(f"  skipped ids (needs review): {stats.skipped_ids[:10]}")


if __name__ == "__main__":
    asyncio.run(main())
