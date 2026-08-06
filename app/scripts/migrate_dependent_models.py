"""Reshape satellite documents for the unified message/relationship model.

One-shot and dry-run by default. Take a database snapshot before applying:
the migration removes legacy fields after deriving their replacements, so the
snapshot is the rollback path.

Migrates:
  * polls: drop ``conversation_id`` and resolve the required ``message_id``;
  * saved messages: drop the redundant ``conversation_id``;
  * calls: backfill the conversation from call history or the participants' DM;
  * notifications: replace ``source_*``/``conversation_id`` with generic refs
    and remap every kind into the final vocabulary;
  * invite links: rename approval/use fields and add ``role_ids``.
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

from bson import ObjectId
from pymongo import AsyncMongoClient, UpdateOne

from app.core.config import settings
from app.db.collections import (
    COL_CALLS,
    COL_CONVERSATIONS,
    COL_INVITE_LINKS,
    COL_MESSAGES,
    COL_NOTIFICATIONS,
    COL_POLLS,
    COL_SAVED_MESSAGES,
)

_FINAL_NOTIFICATION_KINDS = {
    "connection_request",
    "connection_accepted",
    "follow",
    "follow_request",
    "membership_invite",
    "membership_approved",
    "message",
    "mention",
    "comment",
    "comment_reply",
    "thread_reply",
    "reaction",
}
_LEGACY_NOTIFICATION_KINDS = {
    "ping_received": "connection_request",
    "ping_accepted": "connection_accepted",
    "space_invite": "membership_invite",
    "join_request": "membership_invite",
    "join_approved": "membership_approved",
}


@dataclass
class DependentModelsMigrationStats:
    polls_scanned: int = 0
    polls_planned: int = 0
    saved_messages_scanned: int = 0
    saved_messages_planned: int = 0
    calls_scanned: int = 0
    calls_planned: int = 0
    notifications_scanned: int = 0
    notifications_planned: int = 0
    invite_links_scanned: int = 0
    invite_links_planned: int = 0
    documents_written: int = 0
    skipped_ids: list[str] = field(default_factory=list)


def _id_candidates(value: Any) -> list[Any]:
    candidates = [value]
    if value is not None:
        string_value = str(value)
        if string_value != value:
            candidates.append(string_value)
        if ObjectId.is_valid(string_value):
            object_id = ObjectId(string_value)
            if object_id not in candidates:
                candidates.append(object_id)
    return candidates


async def _poll_updates(
    db: Any, stats: DependentModelsMigrationStats
) -> list[UpdateOne]:
    updates: list[UpdateOne] = []
    async for poll in db[COL_POLLS].find({}):
        stats.polls_scanned += 1
        message_id = poll.get("message_id")
        if not message_id:
            message = await db[COL_MESSAGES].find_one(
                {
                    "content.plaintext.poll_ref.poll_id": {
                        "$in": _id_candidates(poll["_id"])
                    }
                },
                {"_id": 1},
            )
            if message is None:
                stats.skipped_ids.append(f"poll:{poll['_id']}")
                continue
            message_id = str(message["_id"])
        updates.append(
            UpdateOne(
                {"_id": poll["_id"]},
                {
                    "$set": {
                        "message_id": str(message_id),
                        "updated_at": datetime.now(UTC),
                    },
                    "$unset": {"conversation_id": ""},
                },
            )
        )
        stats.polls_planned += 1
    return updates


async def _saved_message_updates(
    db: Any, stats: DependentModelsMigrationStats
) -> list[UpdateOne]:
    updates: list[UpdateOne] = []
    async for saved in db[COL_SAVED_MESSAGES].find(
        {"conversation_id": {"$exists": True}}, {"_id": 1}
    ):
        stats.saved_messages_scanned += 1
        updates.append(
            UpdateOne(
                {"_id": saved["_id"]},
                {
                    "$set": {"updated_at": datetime.now(UTC)},
                    "$unset": {"conversation_id": ""},
                },
            )
        )
        stats.saved_messages_planned += 1
    return updates


async def _call_conversation_id(db: Any, call: dict[str, Any]) -> str | None:
    history_message_id = call.get("history_message_id")
    if history_message_id:
        message = await db[COL_MESSAGES].find_one(
            {"_id": {"$in": _id_candidates(history_message_id)}},
            {"container_type": 1, "container_id": 1},
        )
        if message and message.get("container_type") == "conversation":
            return str(message["container_id"])

    participant_ids = [str(value) for value in call.get("participant_user_ids") or []]
    if len(participant_ids) != 2:
        return None
    conversation = await db[COL_CONVERSATIONS].find_one(
        {
            "type": "dm",
            "participant_ids": {"$all": participant_ids, "$size": 2},
        },
        {"_id": 1},
    )
    return str(conversation["_id"]) if conversation is not None else None


async def _call_updates(
    db: Any, stats: DependentModelsMigrationStats
) -> list[UpdateOne]:
    updates: list[UpdateOne] = []
    async for call in db[COL_CALLS].find(
        {"conversation_id": {"$in": [None, ""]}}
    ):
        stats.calls_scanned += 1
        conversation_id = await _call_conversation_id(db, call)
        if conversation_id is None:
            stats.skipped_ids.append(f"call:{call['_id']}")
            continue
        updates.append(
            UpdateOne(
                {"_id": call["_id"]},
                {
                    "$set": {
                        "conversation_id": conversation_id,
                        "updated_at": datetime.now(UTC),
                    }
                },
            )
        )
        stats.calls_planned += 1
    return updates


def _notification_kind(notification: dict[str, Any]) -> str:
    current = str(notification.get("kind") or "")
    if current in _FINAL_NOTIFICATION_KINDS:
        if current == "message" and (notification.get("data") or {}).get("mention"):
            return "mention"
        return current
    return _LEGACY_NOTIFICATION_KINDS.get(current, "message")


def _notification_refs(
    notification: dict[str, Any],
) -> tuple[str | None, str | None, str | None, str | None]:
    data = notification.get("data") or {}
    source_type = notification.get("source_type")
    source_id = notification.get("source_id")
    conversation_id = notification.get("conversation_id")

    actor_user_id = (
        notification.get("actor_user_id")
        or data.get("actor_user_id")
        or data.get("sender_id")
        or data.get("peer_user_id")
        or data.get("invited_by")
    )
    message_id = notification.get("message_id")
    if source_type == "message":
        message_id = message_id or source_id
        resource_type = "conversation" if conversation_id else None
        resource_id = conversation_id
    elif source_type == "ping":
        resource_type = "user"
        resource_id = data.get("peer_user_id") or source_id
    elif source_type in {"user", "conversation", "channel", "space"}:
        resource_type = source_type
        resource_id = source_id or conversation_id
    elif conversation_id:
        resource_type = "conversation"
        resource_id = conversation_id
    else:
        resource_type = notification.get("resource_type")
        resource_id = notification.get("resource_id")

    return (
        str(actor_user_id) if actor_user_id else None,
        str(resource_type) if resource_type else None,
        str(resource_id) if resource_id else None,
        str(message_id) if message_id else None,
    )


async def _notification_updates(
    db: Any, stats: DependentModelsMigrationStats
) -> list[UpdateOne]:
    updates: list[UpdateOne] = []
    async for notification in db[COL_NOTIFICATIONS].find({}):
        stats.notifications_scanned += 1
        actor_user_id, resource_type, resource_id, message_id = _notification_refs(
            notification
        )
        if actor_user_id is None or resource_type is None or resource_id is None:
            stats.skipped_ids.append(f"notification:{notification['_id']}")
            continue
        data = dict(notification.get("data") or {})
        current_kind = str(notification.get("kind") or "")
        mapped_kind = _notification_kind(notification)
        if current_kind != mapped_kind:
            data.setdefault("legacy_kind", current_kind)
        updates.append(
            UpdateOne(
                {"_id": notification["_id"]},
                {
                    "$set": {
                        "kind": mapped_kind,
                        "actor_user_id": actor_user_id,
                        "resource_type": resource_type,
                        "resource_id": resource_id,
                        "message_id": message_id,
                        "data": data,
                        "updated_at": datetime.now(UTC),
                    },
                    "$unset": {
                        "source_type": "",
                        "source_id": "",
                        "conversation_id": "",
                    },
                },
            )
        )
        stats.notifications_planned += 1
    return updates


async def _invite_link_updates(
    db: Any, stats: DependentModelsMigrationStats
) -> list[UpdateOne]:
    updates: list[UpdateOne] = []
    async for invite in db[COL_INVITE_LINKS].find({}):
        stats.invite_links_scanned += 1
        updates.append(
            UpdateOne(
                {"_id": invite["_id"]},
                {
                    "$set": {
                        "uses": int(invite.get("uses", invite.get("use_count", 0))),
                        "approval_required": bool(
                            invite.get(
                                "approval_required",
                                invite.get("requires_approval", False),
                            )
                        ),
                        "role_ids": list(invite.get("role_ids") or []),
                        "updated_at": datetime.now(UTC),
                    },
                    "$unset": {"use_count": "", "requires_approval": ""},
                },
            )
        )
        stats.invite_links_planned += 1
    return updates


async def run_migration(
    db: Any, *, apply: bool
) -> DependentModelsMigrationStats:
    stats = DependentModelsMigrationStats()
    updates_by_collection = {
        COL_POLLS: await _poll_updates(db, stats),
        COL_SAVED_MESSAGES: await _saved_message_updates(db, stats),
        COL_CALLS: await _call_updates(db, stats),
        COL_NOTIFICATIONS: await _notification_updates(db, stats),
        COL_INVITE_LINKS: await _invite_link_updates(db, stats),
    }
    if apply and stats.skipped_ids:
        raise RuntimeError(
            "Unresolved dependent documents; refusing a partial migration: "
            f"{stats.skipped_ids[:10]}"
        )
    if apply:
        for collection_name, updates in updates_by_collection.items():
            if not updates:
                continue
            result = await db[collection_name].bulk_write(updates, ordered=False)
            stats.documents_written += int(result.modified_count)
    return stats


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write the new shapes. Without this flag the script is a dry run.",
    )
    parser.add_argument(
        "--snapshot-confirmed",
        action="store_true",
        help="Required with --apply to confirm a rollback snapshot exists.",
    )
    parser.add_argument("--mongo-uri", default=settings.mongo_uri)
    parser.add_argument("--mongo-db", default=settings.mongo_db)
    args = parser.parse_args()
    if args.apply and not args.snapshot_confirmed:
        parser.error("--apply requires --snapshot-confirmed")

    client: AsyncMongoClient = AsyncMongoClient(args.mongo_uri)
    db = client[args.mongo_db]
    try:
        stats = await run_migration(db, apply=args.apply)
    finally:
        await client.close()

    mode = "APPLIED" if args.apply else "DRY RUN (no writes)"
    print(f"[migrate_dependent_models] {mode} against {args.mongo_db}")
    for key, value in asdict(stats).items():
        if key == "skipped_ids":
            continue
        print(f"  {key}: {value}")
    if stats.skipped_ids:
        print(f"  skipped ids (must resolve before apply): {stats.skipped_ids[:10]}")


if __name__ == "__main__":
    asyncio.run(main())
