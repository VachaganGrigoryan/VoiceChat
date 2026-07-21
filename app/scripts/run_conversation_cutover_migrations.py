"""Run the full conversation cutover migration sequence.

Dry-run by default. Run from the backend API service/container so it uses the
same application code and Mongo connectivity as production.
"""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Sequence
from dataclasses import asdict
from typing import Any

from pymongo import AsyncMongoClient

from app.core.config import settings
from app.scripts.cleanup_legacy_message_fields import run_cleanup
from app.scripts.drop_legacy_message_indexes import run_drop_indexes
from app.scripts.migrate_conversation_message_ids import (
    run_migration as run_conversation_id_migration,
)
from app.scripts.migrate_message_receipts import (
    run_migration as run_message_receipt_migration,
)
from app.scripts.repair_call_message_content import (
    run_migration as run_call_message_content_repair,
)


def _print_stats(name: str, stats: Any) -> None:
    print(f"\n[{name}]")
    for key, value in asdict(stats).items():
        print(f"  {key}: {value}")


def _has_items(stats: Any, fields: Sequence[str]) -> bool:
    return any(bool(getattr(stats, field, None)) for field in fields)


async def run_cutover_migrations(
    *,
    mongo_uri: str,
    mongo_db: str,
    apply: bool,
    include_receiver_id: bool,
    allow_skips: bool,
) -> int:
    client: AsyncMongoClient = AsyncMongoClient(mongo_uri)
    db = client[mongo_db]
    has_blockers = False
    try:
        mode = "APPLY" if apply else "DRY RUN"
        print(f"[conversation_cutover] {mode} against {mongo_db}")
        print("  step 1/5: migrate legacy message conversation ids")
        conversation_stats = await run_conversation_id_migration(db, apply=apply)
        _print_stats("migrate_conversation_message_ids", conversation_stats)
        has_blockers = has_blockers or _has_items(
            conversation_stats, ("skipped_keys",)
        )

        print("\n  step 2/5: repair call message content")
        call_content_stats = await run_call_message_content_repair(db, apply=apply)
        _print_stats("repair_call_message_content", call_content_stats)
        has_blockers = has_blockers or _has_items(
            call_content_stats, ("skipped_ids", "duplicate_call_ids")
        )
        if has_blockers:
            print(
                "\n[conversation_cutover] BLOCKED: repair call message content "
                "before continuing."
            )
            return 2 if not allow_skips else 0

        print("\n  step 3/5: backfill message receipts")
        receipt_stats = await run_message_receipt_migration(db, apply=apply)
        _print_stats("migrate_message_receipts", receipt_stats)
        has_blockers = has_blockers or _has_items(receipt_stats, ("skipped_ids",))

        print("\n  step 4/5: drop legacy message indexes")
        index_stats = await run_drop_indexes(db, apply=apply)
        _print_stats("drop_legacy_message_indexes", index_stats)

        print("\n  step 5/5: clean legacy message fields")
        cleanup_stats = await run_cleanup(
            db,
            apply=apply,
            include_receiver_id=include_receiver_id,
        )
        _print_stats("cleanup_legacy_message_fields", cleanup_stats)
        has_blockers = has_blockers or _has_items(cleanup_stats, ("skipped_ids",))
    finally:
        await client.close()

    if has_blockers and not allow_skips:
        print(
            "\n[conversation_cutover] BLOCKED: skipped records need review. "
            "Re-run with --allow-skips only after confirming they are safe."
        )
        return 2

    print("\n[conversation_cutover] complete")
    return 0


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write changes. Without this flag every step runs as a dry run.",
    )
    parser.add_argument(
        "--include-receiver-id",
        action="store_true",
        help=(
            "Unset receiver_id during cleanup. Use for the final cutover after "
            "message receipts have been backfilled."
        ),
    )
    parser.add_argument(
        "--allow-skips",
        action="store_true",
        help="Return success even when skipped records are reported.",
    )
    parser.add_argument("--mongo-uri", default=settings.mongo_uri)
    parser.add_argument("--mongo-db", default=settings.mongo_db)
    args = parser.parse_args()

    exit_code = await run_cutover_migrations(
        mongo_uri=args.mongo_uri,
        mongo_db=args.mongo_db,
        apply=args.apply,
        include_receiver_id=args.include_receiver_id,
        allow_skips=args.allow_skips,
    )
    raise SystemExit(exit_code)


if __name__ == "__main__":
    asyncio.run(main())
