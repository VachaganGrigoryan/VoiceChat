"""Directory queries.

Every query here is *listing* policy — what a caller is allowed to see exists —
which is a different question from `AuthorizationService.can`, i.e. what they may
do with it. Listing rules live here; nothing here decides an action.
"""

from __future__ import annotations

from typing import Any, Sequence

from app.core.pagination.cursor import keyset_condition
from app.db.models import (
    BlockDocument,
    ChannelDocument,
    ConversationDocument,
    RelationshipDocument,
    SpaceDocument,
)


def _escape(term: str) -> str:
    """Escape regex metacharacters so a search term is never a pattern."""
    return "".join("\\" + ch if ch in ".^$*+?()[]{}|\\" else ch for ch in term)


def _text_filter(q: str | None, fields: Sequence[str]) -> dict[str, Any] | None:
    if not q or not q.strip():
        return None
    pattern = {"$regex": _escape(q.strip()), "$options": "i"}
    return {"$or": [{field: pattern} for field in fields]}


class DirectoryRepository:
    async def blocked_user_ids(self, user_id: str) -> set[str]:
        """Every user id on either side of a block with the caller.

        Both directions: a directory must not show you someone you blocked, nor
        someone who blocked you.
        """
        blocks = await BlockDocument.find(
            {"$or": [{"blocker_id": user_id}, {"blocked_id": user_id}]}
        ).to_list()
        ids: set[str] = set()
        for block in blocks:
            ids.add(str(block.blocker_id))
            ids.add(str(block.blocked_id))
        ids.discard(user_id)
        return ids

    async def member_space_ids(self, user_id: str) -> list[str]:
        memberships = await RelationshipDocument.find(
            {
                "kind": "membership",
                "user_id": user_id,
                "target_type": "space",
                "status": "active",
            }
        ).to_list()
        return [str(membership.target_id) for membership in memberships]

    async def viewer_edges(
        self, *, user_id: str, target_type: str, target_ids: Sequence[str]
    ) -> tuple[dict[str, str], set[str]]:
        """Membership status and follow state for a page of entities, in two queries."""
        if not target_ids:
            return {}, set()
        ids = [str(target_id) for target_id in target_ids]
        memberships = await RelationshipDocument.find(
            {
                "kind": "membership",
                "user_id": user_id,
                "target_type": target_type,
                "target_id": {"$in": ids},
            }
        ).to_list()
        follows = await RelationshipDocument.find(
            {
                "kind": "follow",
                "user_id": user_id,
                "target_type": target_type,
                "target_id": {"$in": ids},
                "status": "active",
            }
        ).to_list()
        return (
            {str(m.target_id): m.status for m in memberships},
            {str(f.target_id) for f in follows},
        )

    async def count_space_members(self, space_ids: Sequence[str]) -> dict[str, int]:
        """Member counts for one page of spaces.

        Computed per page rather than denormalized, which is why it cannot order
        the query — see `DirectoryService.list_spaces`.
        """
        if not space_ids:
            return {}
        counts: dict[str, int] = {}
        for space_id in space_ids:
            counts[str(space_id)] = await RelationshipDocument.find(
                {
                    "kind": "membership",
                    "target_type": "space",
                    "target_id": str(space_id),
                    "status": "active",
                }
            ).count()
        return counts

    # --- listing queries ----------------------------------------------------

    async def list_channels(
        self,
        *,
        q: str | None,
        owner_type: str | None,
        space_id: str | None,
        kind: str | None,
        tags: Sequence[str] | None,
        blocked_user_ids: set[str],
        sort_field: str,
        cursor: dict[str, Any] | None,
        limit: int,
    ) -> list[ChannelDocument]:
        conditions: list[dict[str, Any]] = [
            # `private` is unlisted entirely; `members` is listable so that a
            # join request has something to point at.
            {"visibility": {"$in": ["public", "members"]}},
            # A channel nobody may join is not a directory entry.
            {"join_policy": {"$ne": "closed"}},
        ]

        if kind is not None:
            conditions.append({"kind": kind})
        else:
            # Every user owns an auto-created profile channel; listing them by
            # default would bury every real channel.
            conditions.append({"kind": {"$ne": "profile"}})

        if owner_type is not None:
            conditions.append({"owner.type": owner_type})
        if space_id is not None:
            conditions.append({"space_id": str(space_id)})
        if tags:
            conditions.append({"tags": {"$in": [tag.lower() for tag in tags]}})
        if blocked_user_ids:
            conditions.append(
                {
                    "$nor": [
                        {"owner.type": "user", "owner.id": {"$in": list(blocked_user_ids)}}
                    ]
                }
            )
        text = _text_filter(q, ["name", "description", "slug", "tags"])
        if text:
            conditions.append(text)
        if cursor:
            conditions.append(keyset_condition(sort_field, cursor))

        return (
            await ChannelDocument.find({"$and": conditions})
            .sort(f"-{sort_field}", "-_id")
            .limit(limit)
            .to_list()
        )

    async def list_spaces(
        self,
        *,
        q: str | None,
        join_policy: str | None,
        blocked_user_ids: set[str],
        cursor: dict[str, Any] | None,
        limit: int,
    ) -> list[SpaceDocument]:
        conditions: list[dict[str, Any]] = [{"visibility": "public"}]
        if join_policy is not None:
            conditions.append({"join_policy": join_policy})
        if blocked_user_ids:
            conditions.append({"owner_user_id": {"$nin": list(blocked_user_ids)}})
        text = _text_filter(q, ["name", "slug"])
        if text:
            conditions.append(text)
        if cursor:
            conditions.append(keyset_condition("created_at", cursor))

        return (
            await SpaceDocument.find({"$and": conditions})
            .sort("-created_at", "-_id")
            .limit(limit)
            .to_list()
        )

    async def list_groups(
        self,
        *,
        q: str | None,
        space_ids: Sequence[str],
        blocked_user_ids: set[str],
        cursor: dict[str, Any] | None,
        limit: int,
    ) -> list[ConversationDocument]:
        conditions: list[dict[str, Any]] = [
            {"type": "group"},
            # The invariant: a group is discoverable only through a space that
            # publishes it. A space-less ad-hoc group chat is never listed.
            {"space_id": {"$in": [str(space_id) for space_id in space_ids]}},
            {"space_visibility": "space_public"},
        ]
        if blocked_user_ids:
            conditions.append({"created_by": {"$nin": list(blocked_user_ids)}})
        text = _text_filter(q, ["title", "description", "slug"])
        if text:
            conditions.append(text)
        if cursor:
            conditions.append(keyset_condition("created_at", cursor))

        return (
            await ConversationDocument.find({"$and": conditions})
            .sort("-created_at", "-_id")
            .limit(limit)
            .to_list()
        )

    async def find_space_by_slug(self, slug: str) -> SpaceDocument | None:
        return await SpaceDocument.find_one({"slug": slug})


__all__ = ["DirectoryRepository"]
