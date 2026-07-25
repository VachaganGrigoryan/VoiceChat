"""Effective ownership — explicit, inherited, and stronger than RBAC (§18–19, §51).

Ownership is read off the resource itself (`OwnerRef`, or `Space.owner_user_id`)
and never depends on a membership or a role. A space owner is the effective
owner of every channel, group, and conversation the space owns, so managing a
child resource needs no separate membership there.
"""

from __future__ import annotations

from typing import Any, Literal

from app.db.models import (
    ChannelDocument,
    ConversationDocument,
    SpaceDocument,
)
from app.db.object_id import parse_object_id

ResourceType = Literal["space", "conversation", "channel"]

# Actions no owner may bypass. Platform moderation lands in a later change; the
# carve-out exists now so `can()` has a single, testable place to consult (§55).
PLATFORM_SAFETY_ACTIONS: frozenset[str] = frozenset()


class OwnershipResolver:
    """Resolves whether a user effectively owns a resource, with a small cache.

    The cache is per-instance and therefore per-request: `AuthorizationService`
    is constructed per request, so a burst of `can()` calls for one user costs
    one resource load rather than one per check.
    """

    def __init__(self) -> None:
        self._resources: dict[tuple[str, str], Any | None] = {}
        self._owner_ids: dict[tuple[str, str], str | None] = {}

    async def load_resource(
        self, *, resource_type: ResourceType, resource_id: str
    ) -> Any | None:
        key = (resource_type, str(resource_id))
        if key in self._resources:
            return self._resources[key]
        model = {
            "space": SpaceDocument,
            "conversation": ConversationDocument,
            "channel": ChannelDocument,
        }[resource_type]
        try:
            doc = await model.get(parse_object_id(str(resource_id)))
        except Exception:  # noqa: BLE001 - a malformed id is "no such resource"
            doc = None
        self._resources[key] = doc
        return doc

    async def effective_owner_id(
        self, *, resource_type: ResourceType, resource_id: str
    ) -> str | None:
        """The user id that ultimately owns this resource, following space owners.

        A space-owned child resolves through its space to that space's
        ``owner_user_id`` — this is what makes ownership cascade (§19).
        """
        key = (resource_type, str(resource_id))
        if key in self._owner_ids:
            return self._owner_ids[key]

        owner_id = await self._resolve_owner_id(
            resource_type=resource_type, resource_id=resource_id
        )
        self._owner_ids[key] = owner_id
        return owner_id

    async def is_effective_owner(
        self, *, user_id: str, resource_type: ResourceType, resource_id: str
    ) -> bool:
        owner_id = await self.effective_owner_id(
            resource_type=resource_type, resource_id=resource_id
        )
        return owner_id is not None and owner_id == str(user_id)

    def owner_may_bypass(self, action: str) -> bool:
        """Whether owner bypass applies to ``action`` (§55 platform-safety carve-out)."""
        return action not in PLATFORM_SAFETY_ACTIONS

    async def parent_space_id(
        self, *, resource_type: ResourceType, resource_id: str
    ) -> str | None:
        """The space a channel/group belongs to, for parent-scope role resolution."""
        if resource_type == "space":
            return None
        resource = await self.load_resource(
            resource_type=resource_type, resource_id=resource_id
        )
        if resource is None:
            return None
        space_id = getattr(resource, "space_id", None)
        return str(space_id) if space_id else None

    async def _resolve_owner_id(
        self, *, resource_type: ResourceType, resource_id: str
    ) -> str | None:
        resource = await self.load_resource(
            resource_type=resource_type, resource_id=resource_id
        )
        if resource is None:
            return None

        if resource_type == "space":
            return str(resource.owner_user_id) if resource.owner_user_id else None

        owner = getattr(resource, "owner", None)
        if owner is None:
            # DMs are ownerless by construction; so are un-migrated group rows.
            return None
        if owner.type == "user":
            return str(owner.id)
        # owner.type == "space": the space's owner owns the child (§19).
        return await self.effective_owner_id(
            resource_type="space", resource_id=str(owner.id)
        )
