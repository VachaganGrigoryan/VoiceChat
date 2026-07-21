"""Drop legacy message indexes after receipt backfill and before field cleanup."""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass, field
from typing import Any

from pymongo import AsyncMongoClient

from app.core.config import settings
from app.db.collections import COL_MESSAGES

LEGACY_INDEXES = (
    "ux_messages_call_call_id",
    "ix_messages_receiver_createdAt_desc",
    "ix_messages_conversation_receiver_status_createdAt_desc",
)


@dataclass
class DropLegacyMessageIndexStats:
    existing_legacy_indexes: list[str] = field(default_factory=list)
    dropped_indexes: list[str] = field(default_factory=list)


async def run_drop_indexes(db: Any, *, apply: bool) -> DropLegacyMessageIndexStats:
    stats = DropLegacyMessageIndexStats()
    col_messages = db[COL_MESSAGES]
    index_names = set((await col_messages.index_information()).keys())
    stats.existing_legacy_indexes = [
        name for name in LEGACY_INDEXES if name in index_names
    ]
    if apply:
        for name in stats.existing_legacy_indexes:
            await col_messages.drop_index(name)
            stats.dropped_indexes.append(name)
    return stats


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually drop legacy indexes. Without this flag the script is a dry run.",
    )
    parser.add_argument("--mongo-uri", default=settings.mongo_uri)
    parser.add_argument("--mongo-db", default=settings.mongo_db)
    args = parser.parse_args()

    client: AsyncMongoClient = AsyncMongoClient(args.mongo_uri)
    db = client[args.mongo_db]
    try:
        stats = await run_drop_indexes(db, apply=args.apply)
    finally:
        await client.close()

    mode = "APPLIED" if args.apply else "DRY RUN (no writes)"
    print(f"[drop_legacy_message_indexes] {mode} against {args.mongo_db}")
    print(f"  existing legacy indexes: {stats.existing_legacy_indexes}")
    print(f"  dropped indexes:         {stats.dropped_indexes}")


if __name__ == "__main__":
    asyncio.run(main())
