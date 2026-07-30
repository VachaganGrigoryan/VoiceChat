"""Reading back the authorization decision (§56–59).

Clients cannot see `can()`. Without this module their only options are to
re-implement the resolution order — which would drift, and drift in
authorization is a security bug rather than a display bug — or to attempt an
action and interpret the 403. Neither is acceptable for deciding whether to
render a composer.

So this is deliberately a thin loop over `AuthorizationService.can`. It holds no
rules of its own. If a decision looks wrong, the bug is in `service.py` and the
fix belongs there.
"""

from __future__ import annotations

from typing import Any, Iterable, Sequence

from app.db.models import RelationshipDocument
from app.modules.authorization.ownership import ResourceType
from app.modules.authorization.permissions import (
    CALL_CREATE,
    MEMBER_INVITE,
    MEMBER_VIEW,
    MESSAGE_CREATE,
    MESSAGE_DELETE_OWN,
    MESSAGE_EDIT_OWN,
    MESSAGE_PIN,
    MESSAGE_READ,
    POLL_CREATE,
    REACTION_CREATE,
    RESOURCE_MANAGE,
    RESOURCE_VIEW,
    ROLE_VIEW,
    THREAD_CREATE,
    THREAD_REPLY,
    CHANNEL_CREATE,
    GROUP_CREATE,
    is_own_scoped,
)
from app.modules.authorization.service import AuthorizationService

# What a container header and composer need in order to paint without a second
# request. `resource.manage` is here because it decides whether the Manage
# affordance renders at all; leaving it out would cost a follow-up request on
# every container open. The heavier management actions (`member.manage`,
# `role.manage`, `resource.delete`, the `.any` variants) are deliberately absent
# — the management sections ask for those themselves, so the common path stays
# cheap.
DEFAULT_ACTIONS: dict[ResourceType, tuple[str, ...]] = {
    "channel": (
        RESOURCE_VIEW,
        MESSAGE_READ,
        MESSAGE_CREATE,
        THREAD_CREATE,
        THREAD_REPLY,
        REACTION_CREATE,
        MESSAGE_EDIT_OWN,
        MESSAGE_DELETE_OWN,
        POLL_CREATE,
        RESOURCE_MANAGE,
    ),
    "conversation": (
        RESOURCE_VIEW,
        MESSAGE_READ,
        MESSAGE_CREATE,
        THREAD_CREATE,
        THREAD_REPLY,
        REACTION_CREATE,
        MESSAGE_EDIT_OWN,
        MESSAGE_DELETE_OWN,
        MESSAGE_PIN,
        CALL_CREATE,
        POLL_CREATE,
        RESOURCE_MANAGE,
    ),
    "space": (
        RESOURCE_VIEW,
        RESOURCE_MANAGE,
        MEMBER_VIEW,
        MEMBER_INVITE,
        CHANNEL_CREATE,
        GROUP_CREATE,
        ROLE_VIEW,
    ),
}

# One request may not ask about more than this many resources. A screenful of
# feed cards is well inside it; anything larger is a client bug or an attempt to
# enumerate.
MAX_RESOURCES_PER_REQUEST = 50


class ResourceStanding:
    """The viewer's posture toward a resource, which permissions cannot express.

    "Following" versus "Join" versus "Request pending" are three different
    buttons and none of them is a permission — a user with no membership and a
    pending membership may both hold exactly the same (empty) permission set.
    """

    __slots__ = ("is_owner", "membership_status", "is_follower", "role_ids")

    def __init__(
        self,
        *,
        is_owner: bool,
        membership_status: str | None,
        is_follower: bool,
        role_ids: Sequence[str],
    ) -> None:
        self.is_owner = is_owner
        self.membership_status = membership_status
        self.is_follower = is_follower
        self.role_ids = list(role_ids)


class ResourceCapabilities:
    __slots__ = ("resource_type", "resource_id", "allowed", "denied", "standing", "policy")

    def __init__(
        self,
        *,
        resource_type: ResourceType,
        resource_id: str,
        allowed: Sequence[str],
        denied: Sequence[str],
        standing: ResourceStanding,
        policy: dict[str, Any],
    ) -> None:
        self.resource_type = resource_type
        self.resource_id = resource_id
        self.allowed = list(allowed)
        self.denied = list(denied)
        self.standing = standing
        self.policy = policy


_POLICY_FIELDS = (
    "visibility",
    "join_policy",
    "posting_policy",
    "comment_policy",
    "space_visibility",
)


async def affected_viewer_ids(
    *, resource_type: ResourceType, resource_id: str, limit: int = 500
) -> list[str]:
    """Users whose capabilities on a resource may have changed.

    Active members only. A resource-wide policy change also affects strangers in
    principle, but a stranger holds no cached entry to invalidate — they resolve
    capabilities when they first open the resource.
    """
    memberships = await RelationshipDocument.find(
        {
            "kind": "membership",
            "target_type": resource_type,
            "target_id": str(resource_id),
            "status": "active",
        }
    ).limit(limit).to_list()
    return [str(membership.user_id) for membership in memberships]


class CapabilitiesService:
    """Resolves capabilities for a batch of resources in one authorization context.

    Batching is the point: `AuthorizationService` and `OwnershipResolver` memoize
    per instance, and that instance is per request. Fifty resources resolved
    together share those caches; fifty separate requests cannot.
    """

    def __init__(self, *, authorization: AuthorizationService | None = None) -> None:
        self.authorization = authorization or AuthorizationService()

    async def resolve(
        self,
        *,
        user_id: str,
        resources: Sequence[tuple[ResourceType, str]],
        actions: Sequence[str] | None = None,
    ) -> list[ResourceCapabilities]:
        results: list[ResourceCapabilities] = []
        for resource_type, resource_id in resources:
            requested = tuple(actions) if actions else DEFAULT_ACTIONS[resource_type]
            results.append(
                await self._resolve_one(
                    user_id=user_id,
                    resource_type=resource_type,
                    resource_id=str(resource_id),
                    actions=requested,
                )
            )
        return results

    async def _resolve_one(
        self,
        *,
        user_id: str,
        resource_type: ResourceType,
        resource_id: str,
        actions: Iterable[str],
    ) -> ResourceCapabilities:
        allowed: list[str] = []
        denied: list[str] = []

        for action in actions:
            # An `*.own` action asks "may you act on what you wrote?", so it is
            # evaluated with the caller as author. The client still has to check
            # authorship per message before offering the affordance.
            sender_id = user_id if is_own_scoped(action) else None
            granted = await self.authorization.can(
                user_id,
                action,
                resource_type,
                resource_id,
                sender_id=sender_id,
            )
            (allowed if granted else denied).append(action)

        return ResourceCapabilities(
            resource_type=resource_type,
            resource_id=resource_id,
            allowed=allowed,
            denied=denied,
            standing=await self._standing(
                user_id=user_id, resource_type=resource_type, resource_id=resource_id
            ),
            policy=await self._policy_echo(
                resource_type=resource_type, resource_id=resource_id
            ),
        )

    async def _standing(
        self, *, user_id: str, resource_type: ResourceType, resource_id: str
    ) -> ResourceStanding:
        """Descriptive only — never an authorization decision.

        Reading the membership directly is safe here precisely because nothing
        is decided from it; the decision came from `can()` above.
        """
        membership = await RelationshipDocument.find_one(
            {
                "kind": "membership",
                "user_id": user_id,
                "target_type": resource_type,
                "target_id": resource_id,
            }
        )
        follow = None
        if resource_type == "channel":
            follow = await RelationshipDocument.find_one(
                {
                    "kind": "follow",
                    "user_id": user_id,
                    "target_type": "channel",
                    "target_id": resource_id,
                    "status": "active",
                }
            )

        return ResourceStanding(
            is_owner=await self.authorization.ownership.is_effective_owner(
                user_id=user_id, resource_type=resource_type, resource_id=resource_id
            ),
            membership_status=membership.status if membership is not None else None,
            is_follower=follow is not None,
            role_ids=[str(role_id) for role_id in (membership.role_ids if membership else [])],
        )

    async def _policy_echo(
        self, *, resource_type: ResourceType, resource_id: str
    ) -> dict[str, Any]:
        """The resource's own policy fields, so management forms need no second fetch.

        Clients render and edit these; they must never gate on them.
        """
        resource = await self.authorization.ownership.load_resource(
            resource_type=resource_type, resource_id=resource_id
        )
        if resource is None:
            return {}
        echo: dict[str, Any] = {}
        for field in _POLICY_FIELDS:
            value = getattr(resource, field, None)
            if value is not None:
                echo[field] = value
        return echo
