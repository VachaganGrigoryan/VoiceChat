"""`AuthorizationService.can` — the single authorization decision point (§56–59).

Nothing else in the codebase may decide access from a role name. Callers ask a
question in the permission vocabulary ("may this user do `resource.manage` on
this channel?") and this service answers it with the fixed resolution order.
"""

from __future__ import annotations

from typing import Any

from app.core.errors import AppError
from app.db.models import (
    BlockDocument,
    RelationshipDocument,
    RoleDocument,
    UserDocument,
)
from app.db.object_id import parse_object_id
from app.modules.authorization.ownership import OwnershipResolver, ResourceType
from app.modules.authorization.permissions import (
    ANY_EQUIVALENT,
    MEMBER_INVITE,
    MESSAGE_CREATE,
    MESSAGE_DELETE_ANY,
    MESSAGE_READ,
    REACTION_CREATE,
    RESOURCE_MANAGE,
    RESOURCE_VIEW,
    THREAD_CREATE,
    THREAD_REPLY,
    is_own_scoped,
)
from app.modules.authorization.repository import RolesRepository

# Actions governed by `posting_policy` — "who in general may put content here".
_POSTING_ACTIONS: frozenset[str] = frozenset({MESSAGE_CREATE, THREAD_CREATE})
# Actions governed by `comment_policy` — replies and reactions to existing content.
_COMMENT_ACTIONS: frozenset[str] = frozenset({THREAD_REPLY, REACTION_CREATE})
# Actions governed by `visibility` / `read_policy`.
_READ_ACTIONS: frozenset[str] = frozenset({RESOURCE_VIEW, MESSAGE_READ})

# A block between two users suspends interaction but not visibility, so reads
# survive and everything else is denied.
_BLOCK_EXEMPT_ACTIONS: frozenset[str] = _READ_ACTIONS

# A space role's power over a child resource (§53): holding `channel.manage` on
# the space is holding `resource.manage` on its channels.
_PARENT_SCOPE_GRANTS: dict[ResourceType, dict[str, str]] = {
    "channel": {
        "resource.view": "channel.manage",
        "resource.manage": "channel.manage",
        "resource.delete": "channel.delete",
    },
    "conversation": {
        "resource.view": "group.manage",
        "resource.manage": "group.manage",
    },
}


class AuthorizationService:
    """Answers `can(user, action, resource)` in the §57 resolution order.

    Construct one per request: membership, role, and resource lookups are
    memoized on the instance, so repeated checks during a single request cost
    one round trip each rather than one per check.
    """

    def __init__(
        self,
        *,
        ownership: OwnershipResolver | None = None,
        roles_repo: RolesRepository | None = None,
    ) -> None:
        self.ownership = ownership or OwnershipResolver()
        self.roles_repo = roles_repo or RolesRepository()
        self._memberships: dict[tuple[str, str, str], RelationshipDocument | None] = {}
        self._role_docs: dict[str, RoleDocument] = {}
        self._users: dict[str, UserDocument | None] = {}

    async def can(
        self,
        user_id: str,
        action: str,
        resource_type: ResourceType,
        resource_id: str,
        *,
        sender_id: str | None = None,
    ) -> bool:
        """Whether ``user_id`` may perform ``action`` on the resource.

        ``sender_id`` is the author of the content being acted on; it is what
        resolves ``*.own`` actions (§59). Positional arguments match the
        contract in §56 so call sites read like the spec.
        """
        user_id = str(user_id)
        resource_id = str(resource_id)

        # 1. User/platform status. An unknown user has no standing at all.
        if await self._load_user(user_id) is None:
            return False

        # An `*.own` action is a claim about authorship; without it, only the
        # `.any` sibling can authorize, and the caller should ask for that.
        if is_own_scoped(action):
            if sender_id is None or str(sender_id) != user_id:
                return False

        resource = await self.ownership.load_resource(
            resource_type=resource_type, resource_id=resource_id
        )
        if resource is None:
            return False

        # 2. Block/safety rules.
        if action not in _BLOCK_EXEMPT_ACTIONS and await self._is_blocked(
            user_id=user_id, resource_type=resource_type, resource=resource
        ):
            return False

        # 3. Effective ownership — bypasses RBAC and policy alike (§51, §55).
        if self.ownership.owner_may_bypass(action) and await self.ownership.is_effective_owner(
            user_id=user_id, resource_type=resource_type, resource_id=resource_id
        ):
            return True

        # 4. Active membership, if one exists. Absence is not yet a denial: a
        #    public resource can still grant reads at steps 9–10.
        membership = await self._active_membership(
            user_id=user_id, resource_type=resource_type, resource_id=resource_id
        )

        # 5. Explicit deny override — beats every grant below it.
        overrides = membership.permission_overrides if membership is not None else None
        if overrides is not None and action in set(overrides.deny):
            return False

        # 6. Explicit allow override.
        if overrides is not None and self._override_allows(
            allow=set(overrides.allow), action=action
        ):
            return True

        # 7. Resource roles + 8. parent-space roles, unioned (§55 union with
        #    deny-precedence; the deny override above already had its say).
        effective = await self._role_permissions(membership)
        effective |= await self._parent_space_permissions(
            user_id=user_id, resource_type=resource_type, resource_id=resource_id
        )

        # Policy caps what roles may grant: when a resource restricts an action
        # to a narrower audience than this user is in, no role widens it back
        # (the §55 `roles > policy` priority orders *grants*, and this is a
        # restriction). Owner bypass and an explicit allow already ran above.
        if self._policy_restricts(
            action=action,
            resource_type=resource_type,
            resource=resource,
            is_member=membership is not None,
            effective=effective,
        ):
            return False

        if self._grants(effective, action):
            return True

        # 9. Resource interaction policy as a grant (open posting/commenting).
        if self._policy_grants(
            action=action,
            resource_type=resource_type,
            resource=resource,
            is_member=membership is not None,
        ):
            return True

        # 10. Public visibility.
        if action in _READ_ACTIONS and self._is_publicly_visible(
            resource_type=resource_type, resource=resource
        ):
            return True

        # 11. Default deny.
        return False

    async def require(
        self,
        user_id: str,
        action: str,
        resource_type: ResourceType,
        resource_id: str,
        *,
        sender_id: str | None = None,
        message: str | None = None,
    ) -> None:
        """`can` as a guard: raises 403 instead of returning False."""
        allowed = await self.can(
            user_id, action, resource_type, resource_id, sender_id=sender_id
        )
        if not allowed:
            raise AppError(
                code="FORBIDDEN",
                message=message or f"Not permitted: {action}",
                status_code=403,
            )

    # --- resolution helpers --------------------------------------------------

    async def _load_user(self, user_id: str) -> UserDocument | None:
        if user_id in self._users:
            return self._users[user_id]
        try:
            user = await UserDocument.get(parse_object_id(user_id))
        except AppError:
            user = None
        self._users[user_id] = user
        return user

    async def _active_membership(
        self, *, user_id: str, resource_type: ResourceType, resource_id: str
    ) -> RelationshipDocument | None:
        """The caller's membership, but only when `active` (§58)."""
        key = (user_id, resource_type, resource_id)
        if key in self._memberships:
            return self._memberships[key]
        doc = await RelationshipDocument.find_one(
            {
                "kind": "membership",
                "user_id": user_id,
                "target_type": resource_type,
                "target_id": resource_id,
                "status": "active",
            }
        )
        self._memberships[key] = doc
        return doc

    async def _role_permissions(
        self, membership: RelationshipDocument | None
    ) -> set[str]:
        """Union of the permissions on a membership's roles (§55 union semantics)."""
        if membership is None or not membership.role_ids:
            return set()
        role_ids = [str(role_id) for role_id in membership.role_ids]
        missing = [role_id for role_id in role_ids if role_id not in self._role_docs]
        if missing:
            for role in await self.roles_repo.list_by_ids(missing):
                self._role_docs[role.str_id] = role
        granted: set[str] = set()
        for role_id in role_ids:
            role = self._role_docs.get(role_id)
            if role is not None:
                granted.update(role.permissions)
        return granted

    async def _parent_space_permissions(
        self, *, user_id: str, resource_type: ResourceType, resource_id: str
    ) -> set[str]:
        """What the caller's space roles grant over this child resource (§53).

        Space-scoped powers translate: `channel.manage` on the space *is*
        `resource.manage` on its channels, so no duplicate role assignment is
        needed on the child.
        """
        space_id = await self.ownership.parent_space_id(
            resource_type=resource_type, resource_id=resource_id
        )
        if space_id is None:
            return set()
        space_membership = await self._active_membership(
            user_id=user_id, resource_type="space", resource_id=space_id
        )
        if space_membership is None:
            return set()
        granted = await self._role_permissions(space_membership)
        for child_action, space_permission in _PARENT_SCOPE_GRANTS.get(
            resource_type, {}
        ).items():
            if space_permission in granted:
                granted.add(child_action)
        overrides = space_membership.permission_overrides
        if overrides is not None:
            granted -= set(overrides.deny)
        return granted

    @staticmethod
    def _grants(granted: set[str], action: str) -> bool:
        if action in granted:
            return True
        # Holding the unrestricted form satisfies the own-scoped request.
        any_form = ANY_EQUIVALENT.get(action)
        return any_form is not None and any_form in granted

    @staticmethod
    def _override_allows(*, allow: set[str], action: str) -> bool:
        if action in allow:
            return True
        any_form = ANY_EQUIVALENT.get(action)
        return any_form is not None and any_form in allow

    # --- policy (§59) --------------------------------------------------------

    def _policy_restricts(
        self,
        *,
        action: str,
        resource_type: ResourceType,
        resource: Any,
        is_member: bool,
        effective: set[str],
    ) -> bool:
        """Whether the resource's own policy puts ``action`` out of reach.

        Policy answers "who in general may interact"; when it names a narrower
        audience than this user is in, the action is capped no matter what a
        role says. The owner has already been let through by step 3, so
        `posting_policy = owner` closes the door on everyone still here.
        """
        if action in _POSTING_ACTIONS:
            posting = getattr(resource, "posting_policy", None)
            if posting == "owner":
                return True
            if posting == "admins":
                return RESOURCE_MANAGE not in effective
            if posting == "moderators":
                return not (
                    RESOURCE_MANAGE in effective or MESSAGE_DELETE_ANY in effective
                )
            if posting == "members" and not is_member:
                return True

        if action in _COMMENT_ACTIONS:
            comment = getattr(resource, "comment_policy", None)
            if comment == "disabled":
                return True
            if comment == "members" and not is_member:
                return True

        if action == MEMBER_INVITE:
            # A closed resource accepts no new members, whoever is asking.
            if getattr(resource, "join_policy", None) == "closed":
                return True

        if action in _READ_ACTIONS and resource_type == "channel":
            if getattr(resource, "visibility", None) == "private" and not is_member:
                return True

        return False

    def _policy_grants(
        self,
        *,
        action: str,
        resource_type: ResourceType,
        resource: Any,
        is_member: bool,
    ) -> bool:
        """Policy as a grant: an open resource lets anyone interact (§59)."""
        if action in _READ_ACTIONS and is_member:
            # `visibility`/`read_policy = members` says exactly this: an active
            # member reads. DMs and private groups rely on it — they seed no
            # roles, and reading is what membership means there.
            return True
        if is_own_scoped(action) and is_member and resource_type == "conversation":
            # Acting on one's own message needs no role in a conversation: a DM
            # seeds none at all (§51), and the `.own` gate has already
            # established authorship. Moderating *others*' messages still needs
            # the `.any` sibling, which only a role grants.
            return True
        if action in _POSTING_ACTIONS:
            if getattr(resource, "posting_policy", None) == "everyone":
                return self._is_publicly_visible(
                    resource_type=resource_type, resource=resource
                ) or is_member
        if action in _COMMENT_ACTIONS:
            comment_policy = getattr(resource, "comment_policy", None)
            if comment_policy is None:
                # Only a channel separates commenting from posting. A DM or group
                # has no comment audience of its own: replying in a thread and
                # reacting are what membership there means.
                return is_member
            if comment_policy == "everyone":
                return self._is_publicly_visible(
                    resource_type=resource_type, resource=resource
                ) or is_member
        return False

    @staticmethod
    def _is_publicly_visible(*, resource_type: ResourceType, resource: Any) -> bool:
        visibility = getattr(resource, "visibility", None)
        if resource_type == "channel":
            if visibility == "public":
                return True
            return False
        if visibility == "public":
            return True
        # Conversations carry a separate read gate for their feed.
        return getattr(resource, "read_policy", None) == "public"

    # --- block/safety --------------------------------------------------------

    async def _is_blocked(
        self, *, user_id: str, resource_type: ResourceType, resource: Any
    ) -> bool:
        """Whether a user↔user block suspends interaction in a DM.

        Only DMs have a single, unambiguous counterparty; blocks do not gate
        group, channel, or space actions.
        """
        if resource_type != "conversation" or getattr(resource, "type", None) != "dm":
            return False
        peers = [str(pid) for pid in resource.participant_ids if str(pid) != user_id]
        if not peers:
            return False
        block = await BlockDocument.find_one(
            {
                "$or": [
                    {"blocker_id": user_id, "blocked_id": {"$in": peers}},
                    {"blocker_id": {"$in": peers}, "blocked_id": user_id},
                ]
            }
        )
        return block is not None
