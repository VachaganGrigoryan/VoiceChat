"""Fold pings, participants, space members, and join requests into ``relationships``.

One-shot and dry-run by default (design "Migration Plan"). Each legacy row
becomes a `RelationshipDocument`; the legacy collections are left in place and
readable so the cutover can be verified (and rolled back from a snapshot)
before ``dependent-models-cleanup`` drops them.

Mappings:
  1. ``pings``                   -> kind=connection  (pair_id preserved)
  2. ``conversation_participants``-> kind=membership, target_type=conversation
                                     (+ inbox state); role ``subscriber``
                                     -> kind=follow, target_type=channel
  3. ``space_members``           -> kind=membership, target_type=space
  4. ``join_requests``           -> pending memberships, matching ``initiation``
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

from pymongo import AsyncMongoClient, UpdateOne

from app.core.config import settings
from app.db.collections import (
    COL_CONVERSATION_PARTICIPANTS,
    COL_JOIN_REQUESTS,
    COL_PINGS,
    COL_RELATIONSHIPS,
    COL_SPACE_MEMBERS,
)

# Legacy ping statuses -> relationship lifecycle. ``blocked`` is intentionally
# absent: blocking lives in ``blocks`` (design §69, Open Questions).
_PING_STATUS: dict[str, str] = {
    "pending": "pending",
    "accepted": "active",
    "declined": "declined",
    "cancelled": "revoked",
    "expired": "revoked",
}
_JOIN_REQUEST_STATUS: dict[str, str] = {
    "pending": "pending",
    "approved": "active",
    "rejected": "declined",
}
_INBOX_STATE_FIELDS: tuple[str, ...] = (
    "last_read_at",
    "last_read_message_id",
    "notification_level",
    "muted_until",
    "archived",
    "pinned",
    "folder",
    "draft_text",
    "draft_updated_at",
    "hidden",
)


# `(kind, user_id, target_type, target_id)` — the unique slot — plus its write.
_KeyedUpdate = tuple[tuple[str, str, str, str], UpdateOne]


@dataclass
class RelationshipMigrationStats:
    pings_scanned: int = 0
    connections_planned: int = 0
    participants_scanned: int = 0
    conversation_memberships_planned: int = 0
    channel_follows_planned: int = 0
    space_members_scanned: int = 0
    space_memberships_planned: int = 0
    join_requests_scanned: int = 0
    join_request_memberships_planned: int = 0
    relationships_written: int = 0
    skipped_blocked_pings: int = 0
    skipped_ids: list[str] = field(default_factory=list)


def _pair_id_for(user_a: str, user_b: str) -> str:
    a, b = str(user_a), str(user_b)
    return f"{a}_{b}" if a < b else f"{b}_{a}"


def _state_from_participant(participant: dict[str, Any]) -> dict[str, Any]:
    state = {
        key: participant[key]
        for key in _INBOX_STATE_FIELDS
        if participant.get(key) is not None
    }
    # `muted` predates `notification_level`; treat it as level=none (§65).
    if participant.get("muted") and "notification_level" not in state:
        state["notification_level"] = "none"
    return state


def _upsert(
    *,
    kind: str,
    user_id: str,
    target_type: str,
    target_id: str,
    status: str,
    initiation: str,
    initiated_by: str,
    created_at: datetime,
    updated_at: datetime,
    approved_by: str | None = None,
    activated_at: datetime | None = None,
    ended_at: datetime | None = None,
    pair_id: str | None = None,
    role_ids: list[str] | None = None,
    state: dict[str, Any] | None = None,
) -> tuple[tuple[str, str, str, str], UpdateOne]:
    """One idempotent upsert plus the unique slot it targets.

    The key is the `(kind, user_id, target_type, target_id)` tuple the unique
    index enforces, so callers can dedupe before writing.
    """
    query: dict[str, Any] = (
        {"kind": kind, "pair_id": pair_id}
        if kind == "connection"
        else {
            "kind": kind,
            "user_id": str(user_id),
            "target_type": target_type,
            "target_id": str(target_id),
        }
    )
    document: dict[str, Any] = {
        "kind": kind,
        "user_id": str(user_id),
        "target_type": target_type,
        "target_id": str(target_id),
        "status": status,
        "initiation": initiation,
        "initiated_by": str(initiated_by),
        "approved_by": str(approved_by) if approved_by else None,
        "requested_at": created_at,
        "activated_at": activated_at,
        "ended_at": ended_at,
        "pair_id": pair_id,
        "role_ids": [str(role_id) for role_id in (role_ids or [])],
        "permission_overrides": None,
        "state": state or {},
        "created_at": created_at,
        "updated_at": updated_at,
    }
    key = (kind, str(user_id), target_type, str(target_id))
    return key, UpdateOne(query, {"$set": document}, upsert=True)


async def _migrate_pings(
    db: Any, stats: RelationshipMigrationStats
) -> list[_KeyedUpdate]:
    updates: list[_KeyedUpdate] = []
    async for ping in db[COL_PINGS].find({}):
        stats.pings_scanned += 1
        legacy_status = str(ping.get("status") or "")
        status = _PING_STATUS.get(legacy_status)
        if status is None:
            # `blocked` pairs are represented by `blocks`, not a connection.
            stats.skipped_blocked_pings += 1
            stats.skipped_ids.append(str(ping["_id"]))
            continue

        from_user_id = str(ping.get("from_user_id") or "")
        to_user_id = str(ping.get("to_user_id") or "")
        if not from_user_id or not to_user_id:
            stats.skipped_ids.append(str(ping["_id"]))
            continue

        responded_at = ping.get("responded_at")
        created_at = ping.get("created_at") or datetime.now(UTC)
        updates.append(
            _upsert(
                kind="connection",
                user_id=from_user_id,
                target_type="user",
                target_id=to_user_id,
                status=status,
                initiation="request",
                initiated_by=from_user_id,
                approved_by=to_user_id if status == "active" else None,
                created_at=created_at,
                updated_at=ping.get("updated_at") or created_at,
                activated_at=responded_at if status == "active" else None,
                ended_at=responded_at if status in {"declined", "revoked"} else None,
                pair_id=ping.get("pair_id") or _pair_id_for(from_user_id, to_user_id),
            )
        )
        stats.connections_planned += 1
    return updates


async def _migrate_participants(
    db: Any, stats: RelationshipMigrationStats
) -> list[_KeyedUpdate]:
    updates: list[_KeyedUpdate] = []
    async for participant in db[COL_CONVERSATION_PARTICIPANTS].find({}):
        stats.participants_scanned += 1
        conversation_id = str(participant.get("conversation_id") or "")
        user_id = str(participant.get("user_id") or "")
        if not conversation_id or not user_id:
            stats.skipped_ids.append(str(participant["_id"]))
            continue

        role = str(participant.get("role") or "member")
        joined_at = participant.get("joined_at") or participant.get("created_at")
        created_at = participant.get("created_at") or joined_at or datetime.now(UTC)
        updated_at = participant.get("updated_at") or created_at
        invited_by = participant.get("invited_by")

        if role == "subscriber":
            # A channel subscriber is feed interest, not participation (§41).
            updates.append(
                _upsert(
                    kind="follow",
                    user_id=user_id,
                    target_type="channel",
                    target_id=conversation_id,
                    status="active",
                    initiation="request",
                    initiated_by=user_id,
                    created_at=created_at,
                    updated_at=updated_at,
                    activated_at=joined_at or created_at,
                    state=_state_from_participant(participant),
                )
            )
            stats.channel_follows_planned += 1
            continue

        updates.append(
            _upsert(
                kind="membership",
                user_id=user_id,
                target_type="conversation",
                target_id=conversation_id,
                status="active",
                initiation="invite" if invited_by else "direct",
                initiated_by=str(invited_by) if invited_by else user_id,
                created_at=created_at,
                updated_at=updated_at,
                activated_at=joined_at or created_at,
                role_ids=[role],
                state=_state_from_participant(participant),
            )
        )
        stats.conversation_memberships_planned += 1
    return updates


async def _migrate_space_members(
    db: Any, stats: RelationshipMigrationStats
) -> list[_KeyedUpdate]:
    updates: list[_KeyedUpdate] = []
    async for member in db[COL_SPACE_MEMBERS].find({}):
        stats.space_members_scanned += 1
        space_id = str(member.get("space_id") or "")
        user_id = str(member.get("user_id") or "")
        if not space_id or not user_id:
            stats.skipped_ids.append(str(member["_id"]))
            continue

        joined_at = member.get("joined_at") or member.get("created_at")
        created_at = member.get("created_at") or joined_at or datetime.now(UTC)
        updates.append(
            _upsert(
                kind="membership",
                user_id=user_id,
                target_type="space",
                target_id=space_id,
                status="active",
                initiation="direct",
                initiated_by=user_id,
                created_at=created_at,
                updated_at=member.get("updated_at") or created_at,
                activated_at=joined_at or created_at,
                role_ids=[str(member.get("role") or "member")],
            )
        )
        stats.space_memberships_planned += 1
    return updates


async def _migrate_join_requests(
    db: Any, stats: RelationshipMigrationStats
) -> list[_KeyedUpdate]:
    updates: list[_KeyedUpdate] = []
    async for request in db[COL_JOIN_REQUESTS].find({}):
        stats.join_requests_scanned += 1
        target_type = str(request.get("target_type") or "")
        target_id = str(request.get("target_id") or "")
        user_id = str(request.get("user_id") or "")
        status = _JOIN_REQUEST_STATUS.get(str(request.get("status") or "pending"))
        if not target_type or not target_id or not user_id or status is None:
            stats.skipped_ids.append(str(request["_id"]))
            continue

        responded_at = request.get("responded_at")
        created_at = request.get("created_at") or datetime.now(UTC)
        # An invite-code request came from an invitation; a bare one is a request.
        initiation = "invite" if request.get("invite_code") else "request"
        updates.append(
            _upsert(
                kind="membership",
                user_id=user_id,
                target_type=target_type,
                target_id=target_id,
                status=status,
                initiation=initiation,
                initiated_by=user_id,
                created_at=created_at,
                updated_at=request.get("updated_at") or created_at,
                activated_at=responded_at if status == "active" else None,
                ended_at=responded_at if status == "declined" else None,
            )
        )
        stats.join_request_memberships_planned += 1
    return updates


async def run_migration(db: Any, *, apply: bool) -> RelationshipMigrationStats:
    stats = RelationshipMigrationStats()

    # Order matters: memberships from `conversation_participants`/`space_members`
    # are `active`, so they must be written before `join_requests` can only ever
    # add rows for users who are not already members.
    membership_updates = await _migrate_participants(db, stats)
    membership_updates += await _migrate_space_members(db, stats)
    already_members = {key for key, _ in membership_updates}

    updates = [update for _, update in await _migrate_pings(db, stats)]
    updates += [update for _, update in membership_updates]
    for key, update in await _migrate_join_requests(db, stats):
        if key in already_members:
            # Already an active member; the resolved request adds nothing.
            stats.join_request_memberships_planned -= 1
            continue
        updates.append(update)

    if apply and updates:
        result = await db[COL_RELATIONSHIPS].bulk_write(updates, ordered=False)
        stats.relationships_written = int(result.upserted_count + result.modified_count)

    return stats


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually write relationships. Without this flag the script is a dry run.",
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
    print(f"[migrate_relationships] {mode} against {args.mongo_db}")
    for key, value in asdict(stats).items():
        if key == "skipped_ids":
            continue
        print(f"  {key}: {value}")
    if stats.skipped_ids:
        print(f"  skipped ids (needs review): {stats.skipped_ids[:10]}")


if __name__ == "__main__":
    asyncio.run(main())
