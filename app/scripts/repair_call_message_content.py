"""Repair canonical call message content before creating call-id indexes.

The ``ux_messages_content_call_call_id`` index is unique for ``type="call"``
messages. Mongo indexes missing/null nested call ids as ``null``, so legacy call
messages must be repaired before Beanie creates the index. This script is a dry
run by default, idempotent, and refuses to write while unresolved or duplicate
call ids remain.
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass, field
from typing import Any

from pymongo import AsyncMongoClient

from app.core.config import settings
from app.db.collections import COL_MESSAGES


@dataclass
class CallMessageContentRepairStats:
    call_messages_scanned: int = 0
    already_valid: int = 0
    content_repaired: int = 0
    skipped_missing_call_payload: int = 0
    duplicate_call_ids: dict[str, list[str]] = field(default_factory=dict)
    skipped_ids: list[str] = field(default_factory=list)
    writes_blocked: bool = False


def _call_id_from(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    call_id = payload.get("call_id")
    if call_id is None:
        return None
    value = str(call_id).strip()
    return value or None


def _nested_call_payload(message: dict[str, Any]) -> dict[str, Any] | None:
    plaintext = message.get("content", {}).get("plaintext")
    if not isinstance(plaintext, dict):
        return None
    call = plaintext.get("call")
    if _call_id_from(call) is None:
        return None
    return call


def _legacy_call_payload(message: dict[str, Any]) -> dict[str, Any] | None:
    call = message.get("call")
    if _call_id_from(call) is None:
        return None
    return call


def _content_with_call(message: dict[str, Any], call: dict[str, Any]) -> dict[str, Any]:
    existing_content = message.get("content")
    content = dict(existing_content) if isinstance(existing_content, dict) else {}
    existing_plaintext = content.get("plaintext")
    plaintext = dict(existing_plaintext) if isinstance(existing_plaintext, dict) else {}
    plaintext.setdefault("text", message.get("text"))
    plaintext.setdefault("media", message.get("media"))
    plaintext["call"] = call
    content["encryption"] = content.get("encryption") or "none"
    content["type"] = "call"
    content["plaintext"] = plaintext
    content.setdefault("ciphertext", None)
    content.setdefault("envelope", None)
    return content


def _has_blockers(stats: CallMessageContentRepairStats) -> bool:
    return bool(stats.skipped_ids or stats.duplicate_call_ids)


async def run_migration(
    db: Any, *, apply: bool
) -> CallMessageContentRepairStats:
    stats = CallMessageContentRepairStats()
    col_messages = db[COL_MESSAGES]
    repairs: list[tuple[Any, dict[str, Any]]] = []
    planned_call_ids: dict[str, list[str]] = {}

    async for message in col_messages.find({"type": "call"}):
        stats.call_messages_scanned += 1
        message_id = str(message["_id"])
        nested_call = _nested_call_payload(message)
        if nested_call is not None:
            stats.already_valid += 1
            call_id = _call_id_from(nested_call)
            if call_id is not None:
                planned_call_ids.setdefault(call_id, []).append(message_id)
            continue

        legacy_call = _legacy_call_payload(message)
        if legacy_call is None:
            stats.skipped_missing_call_payload += 1
            stats.skipped_ids.append(message_id)
            continue

        stats.content_repaired += 1
        call_id = _call_id_from(legacy_call)
        if call_id is not None:
            planned_call_ids.setdefault(call_id, []).append(message_id)
        repairs.append((message["_id"], _content_with_call(message, legacy_call)))

    stats.duplicate_call_ids = {
        call_id: ids for call_id, ids in sorted(planned_call_ids.items()) if len(ids) > 1
    }
    if _has_blockers(stats):
        stats.writes_blocked = bool(apply and repairs)
        return stats

    if apply:
        for message_id, content in repairs:
            await col_messages.update_one(
                {"_id": message_id},
                {"$set": {"content": content}},
            )

    return stats


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually repair content. Without this flag the script is a dry run.",
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
    print(f"[repair_call_message_content] {mode} against {args.mongo_db}")
    print(f"  call messages scanned:        {stats.call_messages_scanned}")
    print(f"  already valid:                {stats.already_valid}")
    print(f"  content repaired:             {stats.content_repaired}")
    print(
        "  skipped missing call payload: "
        f"{stats.skipped_missing_call_payload}"
    )
    print(f"  duplicate call ids:           {stats.duplicate_call_ids}")
    print(f"  writes blocked:               {stats.writes_blocked}")
    if stats.skipped_ids:
        print(f"  skipped ids (needs review): {stats.skipped_ids[:10]}")

    if _has_blockers(stats):
        raise SystemExit(2)


if __name__ == "__main__":
    asyncio.run(main())
