"""Backfill derived fields introduced by finalize-messenger-conversation-model.

For existing conversations and participants created before the model was
generalized, this script sets the new default-bearing fields so queries that read
them behave consistently:

- Conversations missing ``visibility`` / ``posting_policy`` get the defaults
  (``private`` / ``everyone``), and ``member_count`` is set from the length of
  ``participant_ids`` when absent.
- Participants missing ``notification_level`` get it derived from the legacy
  ``muted`` flag (``muted=True`` -> ``none``, else ``all``).

No message-id rewriting and no destructive changes happen here; widening the
conversation ``type`` enum needs no data change. Safe by default: DRY RUN unless
``--apply`` is passed. Idempotent: only documents missing a field are touched, so
a second run reports zero changes.
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from typing import Any

from pymongo import AsyncMongoClient

from app.core.config import settings
from app.db.collections import (
    COL_CONVERSATION_PARTICIPANTS,
    COL_CONVERSATIONS,
)


@dataclass
class FinalizeStats:
    conversations_scanned: int = 0
    conversations_defaults_set: int = 0
    conversations_member_count_set: int = 0
    participants_scanned: int = 0
    participants_notification_level_set: int = 0


async def run_finalize(db: Any, *, apply: bool) -> FinalizeStats:
    stats = FinalizeStats()
    col_conversations = db[COL_CONVERSATIONS]
    col_participants = db[COL_CONVERSATION_PARTICIPANTS]

    async for conv in col_conversations.find({}):
        stats.conversations_scanned += 1
        updates: dict[str, Any] = {}
        if "visibility" not in conv:
            updates["visibility"] = "private"
        if "posting_policy" not in conv:
            updates["posting_policy"] = "everyone"
        member_count_missing = "member_count" not in conv
        if member_count_missing:
            updates["member_count"] = len(conv.get("participant_ids", []))
        if updates:
            if member_count_missing:
                stats.conversations_member_count_set += 1
            # Count a doc as "defaults set" when any of the enum defaults were added.
            if "visibility" in updates or "posting_policy" in updates:
                stats.conversations_defaults_set += 1
            if apply:
                await col_conversations.update_one(
                    {"_id": conv["_id"]}, {"$set": updates}
                )

    async for part in col_participants.find({"notification_level": {"$exists": False}}):
        stats.participants_scanned += 1
        level = "none" if part.get("muted") else "all"
        stats.participants_notification_level_set += 1
        if apply:
            await col_participants.update_one(
                {"_id": part["_id"]},
                {"$set": {"notification_level": level}},
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
        stats = await run_finalize(db, apply=args.apply)
    finally:
        await client.close()

    mode = "APPLIED" if args.apply else "DRY RUN (no writes)"
    print(f"[finalize_conversation_model] {mode} against {args.mongo_db}")
    print(f"  conversations scanned:              {stats.conversations_scanned}")
    print(f"  conversations enum-defaults set:    {stats.conversations_defaults_set}")
    print(f"  conversations member_count set:     {stats.conversations_member_count_set}")
    print(f"  participants scanned (no level):    {stats.participants_scanned}")
    print(
        "  participants notification_level set:"
        f" {stats.participants_notification_level_set}"
    )


if __name__ == "__main__":
    asyncio.run(main())
