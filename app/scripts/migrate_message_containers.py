"""Re-address messages by container and flatten promoted thread conversations.

One-shot and dry-run by default (design "Migration Plan" steps 2–3, 5). Must run
*before* the `unified-messages` reader cutover is deployed: every message read is
now keyed on `container_type`/`container_id`, so un-backfilled rows are invisible.

Steps, in order:
  1. backfill `container_type="conversation"`, `container_id=conversation_id` on
     every message that has no container yet (batched);
  2. flatten each `Conversation(type="thread")`: its messages move to the parent
     container with `thread_root_id` set to the thread's root message, ordering
     preserved by `created_at`; the thread conversation rows are then removed;
  3. drop `conversation_id` and the indexes that were keyed on it.

Rollback: restore the pre-run snapshot. Step 3 is what makes the run one-way, so
`--keep-conversation-id` stops after step 2 when a staged cutover is wanted.
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

from pymongo import AsyncMongoClient, UpdateOne

from app.core.config import settings
from app.db.collections import COL_CONVERSATIONS, COL_MESSAGES

BATCH_SIZE = 1000

# Indexes that only existed to serve `conversation_id` lookups. The container
# indexes declared on `MessageDocument` replace them.
_LEGACY_MESSAGE_INDEXES = (
    "ix_messages_conversation_createdAt_desc",
    "ix_messages_conversation_threadRoot_createdAt_desc",
)


@dataclass
class ContainerMigrationStats:
    messages_scanned: int = 0
    messages_backfilled: int = 0
    thread_conversations_scanned: int = 0
    thread_conversations_deleted: int = 0
    thread_messages_rehomed: int = 0
    conversation_id_unset: int = 0
    legacy_indexes_dropped: int = 0
    skipped_ids: list[str] = field(default_factory=list)


async def _flush(col: Any, updates: list[UpdateOne], *, apply: bool) -> int:
    if not updates or not apply:
        return 0
    result = await col.bulk_write(updates, ordered=False)
    return int(result.modified_count)


async def backfill_containers(
    db: Any, stats: ContainerMigrationStats, *, apply: bool
) -> None:
    """Step 1: every un-migrated message gets its conversation container."""
    now = datetime.now(UTC)
    updates: list[UpdateOne] = []
    cursor = db[COL_MESSAGES].find(
        {"container_id": {"$in": [None, ""]}}, {"conversation_id": 1}
    )
    async for message in cursor:
        stats.messages_scanned += 1
        conversation_id = message.get("conversation_id")
        if not conversation_id:
            # No container and no legacy pointer: nothing can address this row.
            stats.skipped_ids.append(str(message["_id"]))
            continue
        updates.append(
            UpdateOne(
                {"_id": message["_id"]},
                {
                    "$set": {
                        "container_type": "conversation",
                        "container_id": str(conversation_id),
                        "updated_at": now,
                    }
                },
            )
        )
        stats.messages_backfilled += 1
        if len(updates) >= BATCH_SIZE:
            await _flush(db[COL_MESSAGES], updates, apply=apply)
            updates = []
    await _flush(db[COL_MESSAGES], updates, apply=apply)


async def flatten_thread_conversations(
    db: Any, stats: ContainerMigrationStats, *, apply: bool
) -> None:
    """Step 2: a promoted thread's messages re-home onto the parent container.

    Ordering is untouched — `created_at` already orders the thread, and the
    parent's own `thread_root_id` pool merges on the same key.
    """
    now = datetime.now(UTC)
    thread_ids: list[Any] = []
    async for thread in db[COL_CONVERSATIONS].find({"type": "thread"}):
        stats.thread_conversations_scanned += 1
        parent_id = thread.get("parent_conversation_id")
        root_message_id = thread.get("root_message_id")
        if not parent_id or not root_message_id:
            stats.skipped_ids.append(str(thread["_id"]))
            continue

        thread_id = str(thread["_id"])
        updates: list[UpdateOne] = []
        cursor = (
            db[COL_MESSAGES]
            .find(
                {
                    "$or": [
                        {"container_type": "conversation", "container_id": thread_id},
                        {"conversation_id": thread_id},
                    ]
                },
                {"_id": 1},
            )
            .sort([("created_at", 1), ("_id", 1)])
        )
        async for message in cursor:
            updates.append(
                UpdateOne(
                    {"_id": message["_id"]},
                    {
                        "$set": {
                            "container_type": "conversation",
                            "container_id": str(parent_id),
                            "conversation_id": str(parent_id),
                            "thread_root_id": str(root_message_id),
                            "reply_mode": "thread",
                            "updated_at": now,
                        }
                    },
                )
            )
            stats.thread_messages_rehomed += 1
            if len(updates) >= BATCH_SIZE:
                await _flush(db[COL_MESSAGES], updates, apply=apply)
                updates = []
        await _flush(db[COL_MESSAGES], updates, apply=apply)
        thread_ids.append(thread["_id"])

    if thread_ids:
        stats.thread_conversations_deleted = len(thread_ids)
        if apply:
            result = await db[COL_CONVERSATIONS].delete_many(
                {"_id": {"$in": thread_ids}}
            )
            stats.thread_conversations_deleted = int(result.deleted_count)
        await _unset_thread_linkage(db, apply=apply)


async def _unset_thread_linkage(db: Any, *, apply: bool) -> None:
    """Drop the thread-as-conversation fields left on surviving conversations."""
    if not apply:
        return
    await db[COL_CONVERSATIONS].update_many(
        {"$or": [{"parent_conversation_id": {"$ne": None}}, {"root_message_id": {"$ne": None}}]},
        {"$unset": {"parent_conversation_id": "", "root_message_id": ""}},
    )
    try:
        await db[COL_CONVERSATIONS].drop_index("ix_conversations_parent_conversation_id")
    except Exception:  # noqa: BLE001 - already absent on a re-run or a fresh db
        pass


async def drop_conversation_id(
    db: Any, stats: ContainerMigrationStats, *, apply: bool
) -> None:
    """Step 3: readers are on `container_*`, so the legacy pointer can go."""
    if not apply:
        # Nothing was written, so the un-migrated rows a dry run still sees are
        # exactly the ones step 1 reported it would backfill.
        stats.conversation_id_unset = await db[COL_MESSAGES].count_documents(
            {"conversation_id": {"$exists": True}}
        )
        return

    remaining = await db[COL_MESSAGES].count_documents(
        {"container_id": {"$in": [None, ""]}}
    )
    if remaining:
        raise RuntimeError(
            f"{remaining} messages still have no container; refusing to drop "
            "conversation_id. Re-run the backfill first."
        )

    result = await db[COL_MESSAGES].update_many(
        {"conversation_id": {"$exists": True}}, {"$unset": {"conversation_id": ""}}
    )
    stats.conversation_id_unset = int(result.modified_count)
    for index_name in _LEGACY_MESSAGE_INDEXES:
        try:
            await db[COL_MESSAGES].drop_index(index_name)
        except Exception:  # noqa: BLE001 - already absent on a re-run or a fresh db
            continue
        stats.legacy_indexes_dropped += 1


async def run_migration(
    db: Any, *, apply: bool, keep_conversation_id: bool = False
) -> ContainerMigrationStats:
    stats = ContainerMigrationStats()
    await backfill_containers(db, stats, apply=apply)
    await flatten_thread_conversations(db, stats, apply=apply)
    if not keep_conversation_id:
        await drop_conversation_id(db, stats, apply=apply)
    return stats


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually write containers and drop conversation_id. Without this "
        "flag it is a dry run.",
    )
    parser.add_argument(
        "--keep-conversation-id",
        action="store_true",
        help="Stop after flattening threads, leaving conversation_id in place "
        "for a staged reader cutover.",
    )
    parser.add_argument("--mongo-uri", default=settings.mongo_uri)
    parser.add_argument("--mongo-db", default=settings.mongo_db)
    args = parser.parse_args()

    client: AsyncMongoClient = AsyncMongoClient(args.mongo_uri)
    db = client[args.mongo_db]
    try:
        stats = await run_migration(
            db, apply=args.apply, keep_conversation_id=args.keep_conversation_id
        )
    finally:
        await client.close()

    mode = "APPLIED" if args.apply else "DRY RUN (no writes)"
    print(f"[migrate_message_containers] {mode} against {args.mongo_db}")
    for key, value in asdict(stats).items():
        if key == "skipped_ids":
            continue
        print(f"  {key}: {value}")
    if stats.skipped_ids:
        print(f"  skipped ids (needs review): {stats.skipped_ids[:10]}")


if __name__ == "__main__":
    asyncio.run(main())
