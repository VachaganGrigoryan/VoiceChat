"""Remove legacy flat message fields after conversation-id migration.

This is the post-cutover cleanup for messages that already have the canonical
``content`` envelope and a real first-class ``conversations._id`` join key.
It is dry-run by default and idempotent.
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass, field
from typing import Any

from bson import ObjectId
from pymongo import AsyncMongoClient

from app.core.config import settings
from app.db.collections import COL_CONVERSATIONS, COL_MESSAGES

LEGACY_MESSAGE_FIELDS = (
    "receiver_id",
    "text",
    "media",
    "call",
    "status",
    "delivered_at",
    "read_at",
)


@dataclass
class LegacyMessageFieldCleanupStats:
    messages_scanned: int = 0
    messages_eligible: int = 0
    messages_cleaned: int = 0
    call_fields_restored: int = 0
    skipped_call_field_due_legacy_index: int = 0
    skipped_invalid_call_content: int = 0
    fields_unset: dict[str, int] = field(default_factory=dict)
    skipped_missing_content: int = 0
    skipped_legacy_or_invalid_conversation_id: int = 0
    skipped_missing_conversation: int = 0
    skipped_ids: list[str] = field(default_factory=list)


def _field_exists_filter(fields: tuple[str, ...]) -> dict[str, Any]:
    return {"$or": [{field_name: {"$exists": True}} for field_name in fields]}


def _call_payload_from_content(message: dict[str, Any]) -> dict[str, Any] | None:
    plaintext = message.get("content", {}).get("plaintext")
    if not isinstance(plaintext, dict):
        return None
    call = plaintext.get("call")
    return call if isinstance(call, dict) and call.get("call_id") else None


async def _has_canonical_conversation(
    col_conversations: Any, conversation_id: str
) -> bool:
    if not ObjectId.is_valid(conversation_id):
        return False
    return (
        await col_conversations.find_one({"_id": ObjectId(conversation_id)}, {"_id": 1})
        is not None
    )


async def run_cleanup(
    db: Any,
    *,
    apply: bool,
    include_receiver_id: bool = False,
) -> LegacyMessageFieldCleanupStats:
    stats = LegacyMessageFieldCleanupStats()
    col_messages = db[COL_MESSAGES]
    col_conversations = db[COL_CONVERSATIONS]
    indexes = await col_messages.index_information()
    preserve_call_field = "ux_messages_call_call_id" in indexes
    fields = (
        LEGACY_MESSAGE_FIELDS
        if include_receiver_id
        else tuple(field for field in LEGACY_MESSAGE_FIELDS if field != "receiver_id")
    )

    async for message in col_messages.find(_field_exists_filter(fields)):
        stats.messages_scanned += 1
        message_id = str(message["_id"])

        if message.get("content") is None:
            stats.skipped_missing_content += 1
            stats.skipped_ids.append(message_id)
            continue

        conversation_id = str(message.get("conversation_id") or "")
        if not ObjectId.is_valid(conversation_id):
            stats.skipped_legacy_or_invalid_conversation_id += 1
            stats.skipped_ids.append(message_id)
            continue

        if not await _has_canonical_conversation(col_conversations, conversation_id):
            stats.skipped_missing_conversation += 1
            stats.skipped_ids.append(message_id)
            continue

        if (
            message.get("type") == "call"
            and "call" in message
            and not preserve_call_field
            and _call_payload_from_content(message) is None
        ):
            stats.skipped_invalid_call_content += 1
            stats.skipped_ids.append(message_id)
            continue

        unset_fields = {
            field: ""
            for field in fields
            if field in message
            and not (field == "call" and preserve_call_field)
            and not (field == "receiver_id" and not include_receiver_id)
        }
        if preserve_call_field and "call" in message:
            stats.skipped_call_field_due_legacy_index += 1
        if not unset_fields:
            continue

        stats.messages_eligible += 1
        for field_name in unset_fields:
            stats.fields_unset[field_name] = stats.fields_unset.get(field_name, 0) + 1

        if apply:
            result = await col_messages.update_one(
                {"_id": message["_id"]},
                {"$unset": unset_fields},
            )
            stats.messages_cleaned += int(result.modified_count)
        else:
            stats.messages_cleaned += 1

    return stats


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually unset fields. Without this flag the script is a dry run.",
    )
    parser.add_argument(
        "--include-receiver-id",
        action="store_true",
        help=(
            "Also unset receiver_id. Use only after message delivery/read status no "
            "longer depends on per-message receiver_id."
        ),
    )
    parser.add_argument("--mongo-uri", default=settings.mongo_uri)
    parser.add_argument("--mongo-db", default=settings.mongo_db)
    args = parser.parse_args()

    client: AsyncMongoClient = AsyncMongoClient(args.mongo_uri)
    db = client[args.mongo_db]
    try:
        stats = await run_cleanup(
            db,
            apply=args.apply,
            include_receiver_id=args.include_receiver_id,
        )
    finally:
        await client.close()

    mode = "APPLIED" if args.apply else "DRY RUN (no writes)"
    print(f"[cleanup_legacy_message_fields] {mode} against {args.mongo_db}")
    print(f"  messages scanned:                         {stats.messages_scanned}")
    print(f"  messages eligible:                        {stats.messages_eligible}")
    print(f"  messages cleaned:                         {stats.messages_cleaned}")
    print(f"  call fields restored:                     {stats.call_fields_restored}")
    print(
        "  call fields skipped due legacy index:     "
        f"{stats.skipped_call_field_due_legacy_index}"
    )
    print(
        "  skipped invalid call content:             "
        f"{stats.skipped_invalid_call_content}"
    )
    print(
        f"  skipped missing content:                  {stats.skipped_missing_content}"
    )
    print(
        "  skipped legacy/invalid conversation id:   "
        f"{stats.skipped_legacy_or_invalid_conversation_id}"
    )
    print(
        f"  skipped missing conversation:             {stats.skipped_missing_conversation}"
    )
    print(f"  fields unset:                             {stats.fields_unset}")
    if stats.skipped_ids:
        print(f"  skipped ids (needs review): {stats.skipped_ids[:10]}")


if __name__ == "__main__":
    asyncio.run(main())
