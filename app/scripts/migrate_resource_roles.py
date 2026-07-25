"""Seed the `roles` collection and point `Relationship.role_ids` at it.

One-shot and dry-run by default (design "Migration Plan" steps 1–2). Must run
*before* the `AuthorizationService` cutover is deployed: until `role_ids` hold
real role ids, `can()` resolves no roles and only owners would pass.

Mappings:
  1. every space / group conversation / channel -> its four system roles
     (Admin, Moderator, Member, Guest). Owner is a bypass, so no Owner role.
  2. `role_ids: ["admin"]` -> `["<Admin role id>"]`; `owner` and `subscriber`
     map to no role (ownership is on the resource; a subscriber is a follow).
  3. a participant's narrowed `permissions` map -> `permission_overrides.deny`
     in the permission vocabulary.
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
    COL_CHANNELS,
    COL_CONVERSATIONS,
    COL_RELATIONSHIPS,
    COL_ROLES,
    COL_SPACES,
)
from app.modules.authorization.permissions import (
    MEMBER_INVITE,
    MEMBER_REMOVE,
    MESSAGE_DELETE_ANY,
    MESSAGE_PIN,
    RESOURCE_MANAGE,
)
from app.modules.authorization.roles import (
    DEFAULT_SYSTEM_ROLES,
    LEGACY_ROLE_NAMES,
    SPACE_ONLY_PERMISSIONS,
)

# The legacy narrowing rights, in the permission vocabulary. The old map could
# only ever *revoke*, so every entry becomes a deny override (§55).
_RIGHT_TO_PERMISSION: dict[str, str] = {
    "can_pin": MESSAGE_PIN,
    "can_invite": MEMBER_INVITE,
    "can_delete": MESSAGE_DELETE_ANY,
    "can_restrict": MEMBER_REMOVE,
    "can_manage": RESOURCE_MANAGE,
}

# `(target_type, target_id)` of every resource whose roles we seed.
_Scope = tuple[str, str]


@dataclass
class RoleMigrationStats:
    spaces_scanned: int = 0
    conversations_scanned: int = 0
    channels_scanned: int = 0
    roles_planned: int = 0
    roles_written: int = 0
    memberships_scanned: int = 0
    memberships_planned: int = 0
    memberships_written: int = 0
    overrides_planned: int = 0
    skipped_ids: list[str] = field(default_factory=list)


def _seeded_permissions(scope_type: str, permissions: frozenset[str]) -> list[str]:
    granted = set(permissions)
    if scope_type != "space":
        granted -= SPACE_ONLY_PERMISSIONS
    return sorted(granted)


async def _collect_scopes(db: Any, stats: RoleMigrationStats) -> list[_Scope]:
    """Every resource that can carry roles.

    DMs are skipped: they have no owner and no roles — both sides are peers.
    """
    scopes: list[_Scope] = []
    async for space in db[COL_SPACES].find({}, {"_id": 1}):
        stats.spaces_scanned += 1
        scopes.append(("space", str(space["_id"])))
    async for conversation in db[COL_CONVERSATIONS].find({}, {"_id": 1, "type": 1}):
        stats.conversations_scanned += 1
        if conversation.get("type") == "dm":
            continue
        scopes.append(("conversation", str(conversation["_id"])))
    async for channel in db[COL_CHANNELS].find({}, {"_id": 1}):
        stats.channels_scanned += 1
        scopes.append(("channel", str(channel["_id"])))
    return scopes


def _role_upserts(
    scopes: list[_Scope], stats: RoleMigrationStats
) -> list[UpdateOne]:
    now = datetime.now(UTC)
    updates: list[UpdateOne] = []
    for scope_type, scope_id in scopes:
        for name, priority, permissions in DEFAULT_SYSTEM_ROLES:
            updates.append(
                UpdateOne(
                    {"scope_type": scope_type, "scope_id": scope_id, "name": name},
                    {
                        "$set": {"updated_at": now},
                        "$setOnInsert": {
                            "scope_type": scope_type,
                            "scope_id": scope_id,
                            "name": name,
                            "permissions": _seeded_permissions(
                                scope_type, permissions
                            ),
                            "priority": priority,
                            "system": True,
                            "created_by": None,
                            "created_at": now,
                        },
                    },
                    upsert=True,
                )
            )
            stats.roles_planned += 1
    return updates


async def _role_ids_by_scope(db: Any) -> dict[tuple[str, str, str], str]:
    """`(scope_type, scope_id, role name) -> role id` for the seeded roles."""
    index: dict[tuple[str, str, str], str] = {}
    async for role in db[COL_ROLES].find({}, {"scope_type": 1, "scope_id": 1, "name": 1}):
        key = (str(role["scope_type"]), str(role["scope_id"]), str(role["name"]))
        index[key] = str(role["_id"])
    return index


def _deny_overrides(existing: Any, permissions: Any) -> dict[str, list[str]] | None:
    """Turn a narrowing `permissions` map into `permission_overrides` (§55).

    The legacy map could only revoke, so a `False` entry becomes a deny and a
    `True` one is simply the role default — never an allow.
    """
    allow = list(existing.get("allow", [])) if isinstance(existing, dict) else []
    deny = list(existing.get("deny", [])) if isinstance(existing, dict) else []
    if isinstance(permissions, dict):
        for right, granted in permissions.items():
            permission = _RIGHT_TO_PERMISSION.get(str(right))
            if permission is not None and not granted and permission not in deny:
                deny.append(permission)
    if not allow and not deny:
        return None
    return {"allow": sorted(allow), "deny": sorted(deny)}


async def _membership_updates(
    db: Any,
    role_ids: dict[tuple[str, str, str], str],
    stats: RoleMigrationStats,
) -> list[UpdateOne]:
    now = datetime.now(UTC)
    updates: list[UpdateOne] = []
    async for membership in db[COL_RELATIONSHIPS].find({"kind": "membership"}):
        stats.memberships_scanned += 1
        target_type = str(membership.get("target_type") or "")
        target_id = str(membership.get("target_id") or "")
        if not target_type or not target_id:
            stats.skipped_ids.append(str(membership["_id"]))
            continue

        known_ids = set(role_ids.values())
        resolved: list[str] = []
        for legacy in membership.get("role_ids") or []:
            value = str(legacy)
            if value in known_ids:
                # Already a role document id — a re-run, so leave it alone.
                if value not in resolved:
                    resolved.append(value)
                continue
            mapped = LEGACY_ROLE_NAMES.get(value.lower())
            if mapped is None:
                # `owner`/`subscriber`, or a name we cannot place: no role.
                # Ownership lives on the resource and a subscriber is a follow.
                continue
            role_id = role_ids.get((target_type, target_id, mapped))
            if role_id is not None and role_id not in resolved:
                resolved.append(role_id)

        overrides = _deny_overrides(
            membership.get("permission_overrides"), membership.get("permissions")
        )
        if overrides is not None:
            stats.overrides_planned += 1

        changed = resolved != [str(v) for v in (membership.get("role_ids") or [])]
        if not changed and overrides == membership.get("permission_overrides"):
            continue

        updates.append(
            UpdateOne(
                {"_id": membership["_id"]},
                {
                    "$set": {
                        "role_ids": resolved,
                        "permission_overrides": overrides,
                        "updated_at": now,
                    },
                    "$unset": {"permissions": ""},
                },
            )
        )
        stats.memberships_planned += 1
    return updates


async def run_migration(db: Any, *, apply: bool) -> RoleMigrationStats:
    stats = RoleMigrationStats()

    scopes = await _collect_scopes(db, stats)
    role_updates = _role_upserts(scopes, stats)
    if apply and role_updates:
        result = await db[COL_ROLES].bulk_write(role_updates, ordered=False)
        stats.roles_written = int(result.upserted_count + result.modified_count)

    # Membership role ids can only be resolved once the roles exist, so a dry
    # run reports zero planned memberships on a database with no roles yet.
    role_ids = await _role_ids_by_scope(db)
    membership_updates = await _membership_updates(db, role_ids, stats)
    if apply and membership_updates:
        result = await db[COL_RELATIONSHIPS].bulk_write(
            membership_updates, ordered=False
        )
        stats.memberships_written = int(result.modified_count)

    return stats


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually write roles and role_ids. Without this flag it is a dry run.",
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
    print(f"[migrate_resource_roles] {mode} against {args.mongo_db}")
    for key, value in asdict(stats).items():
        if key == "skipped_ids":
            continue
        print(f"  {key}: {value}")
    if stats.skipped_ids:
        print(f"  skipped ids (needs review): {stats.skipped_ids[:10]}")


if __name__ == "__main__":
    asyncio.run(main())
