"""Resolver matrix for `AuthorizationService.can` (RefactoringPlan §55–59).

Resources are pre-seeded into the `OwnershipResolver` cache and memberships are
served by a fake `find_one` that honours the status filter, so every test
exercises the real resolution order without a Mongo server.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from beanie import PydanticObjectId

from app.db.models import (
    ChannelDocument,
    ConversationDocument,
    OwnerRef,
    RelationshipDocument,
    RoleDocument,
    SpaceDocument,
    UserDocument,
)
from app.db.models.relationship import RelationshipPermissionOverrides
from app.modules.authorization.ownership import OwnershipResolver
from app.modules.authorization.permissions import (
    CHANNEL_MANAGE,
    MEMBER_INVITE,
    MESSAGE_CREATE,
    MESSAGE_DELETE_ANY,
    MESSAGE_DELETE_OWN,
    MESSAGE_EDIT_OWN,
    MESSAGE_READ,
    RESOURCE_MANAGE,
    RESOURCE_VIEW,
)
from app.modules.authorization.roles import (
    DEFAULT_SYSTEM_ROLES,
    ROLE_ADMIN,
    ROLE_GUEST,
    ROLE_MEMBER,
    ROLE_MODERATOR,
    RoleService,
)
from app.modules.authorization.service import AuthorizationService

OWNER = "6501f77bd4a1c2b3e4f50001"
MEMBER = "6501f77bd4a1c2b3e4f50002"
STRANGER = "6501f77bd4a1c2b3e4f50003"
PEER = "6501f77bd4a1c2b3e4f50004"

SPACE_ID = "6501f77bd4a1c2b3e4f50010"
CHANNEL_ID = "6501f77bd4a1c2b3e4f50011"
CONVERSATION_ID = "6501f77bd4a1c2b3e4f50012"
DM_ID = "6501f77bd4a1c2b3e4f50013"

ROLE_MEMBER_ID = "6501f77bd4a1c2b3e4f50020"
ROLE_MOD_ID = "6501f77bd4a1c2b3e4f50021"
ROLE_SPACE_ADMIN_ID = "6501f77bd4a1c2b3e4f50022"


def make_role(role_id: str, name: str, permissions: set[str], **overrides) -> RoleDocument:
    role = RoleDocument(
        scope_type=overrides.pop("scope_type", "channel"),
        scope_id=overrides.pop("scope_id", CHANNEL_ID),
        name=name,
        permissions=sorted(permissions),
        **overrides,
    )
    role.id = PydanticObjectId(role_id)
    return role


def make_membership(
    *,
    user_id: str,
    target_type: str,
    target_id: str,
    status: str = "active",
    role_ids: list[str] | None = None,
    allow: list[str] | None = None,
    deny: list[str] | None = None,
) -> RelationshipDocument:
    overrides = None
    if allow or deny:
        overrides = RelationshipPermissionOverrides(
            allow=allow or [], deny=deny or []
        )
    return RelationshipDocument(
        kind="membership",
        user_id=user_id,
        target_type=target_type,
        target_id=target_id,
        status=status,
        initiation="direct",
        initiated_by=user_id,
        role_ids=role_ids or [],
        permission_overrides=overrides,
    )


def make_follow(
    *,
    user_id: str,
    target_type: str,
    target_id: str,
    status: str = "active",
) -> RelationshipDocument:
    return RelationshipDocument(
        kind="follow",
        user_id=user_id,
        target_type=target_type,
        target_id=target_id,
        status=status,
        initiation="request",
        initiated_by=user_id,
    )


def make_channel(**overrides) -> ChannelDocument:
    fields = {
        "owner": OwnerRef(type="user", id=OWNER),
        "slug": "general",
        "name": "General",
        "created_by": OWNER,
    }
    fields.update(overrides)
    channel = ChannelDocument(**fields)
    channel.id = PydanticObjectId(CHANNEL_ID)
    return channel


def make_space(**overrides) -> SpaceDocument:
    fields = {
        "name": "Vogi",
        "slug": "vogi",
        "owner_user_id": OWNER,
        "created_by": OWNER,
    }
    fields.update(overrides)
    space = SpaceDocument(**fields)
    space.id = PydanticObjectId(SPACE_ID)
    return space


def make_dm() -> ConversationDocument:
    dm = ConversationDocument(
        type="dm", participant_ids=[MEMBER, PEER], created_by=MEMBER
    )
    dm.id = PydanticObjectId(DM_ID)
    return dm


class _Fixture:
    """A `can()` harness: pre-seeded resources, memberships, roles, and blocks."""

    def __init__(self) -> None:
        self.resources: dict[tuple[str, str], object] = {}
        self.memberships: list[RelationshipDocument] = []
        self.follows: list[RelationshipDocument] = []
        self.roles: dict[str, RoleDocument] = {}
        self.blocked_pairs: set[tuple[str, str]] = set()
        self.known_users: set[str] = {OWNER, MEMBER, STRANGER, PEER}

    def add_resource(self, resource_type: str, resource_id: str, doc: object) -> None:
        self.resources[(resource_type, resource_id)] = doc

    def add_membership(self, membership: RelationshipDocument) -> None:
        self.memberships.append(membership)

    def add_role(self, role: RoleDocument) -> None:
        self.roles[role.str_id] = role

    def add_follow(self, follow: RelationshipDocument) -> None:
        self.follows.append(follow)

    def _find_relationship(self, query: dict) -> RelationshipDocument | None:
        candidates = self.memberships if query["kind"] == "membership" else self.follows
        targets = query.get("$or")
        if targets is None:
            targets = [
                {
                    "target_type": query["target_type"],
                    "target_id": query["target_id"],
                }
            ]
        for doc in candidates:
            if (
                str(doc.user_id) == query["user_id"]
                and doc.status == query["status"]
                and any(
                    doc.target_type == target["target_type"]
                    and str(doc.target_id) == target["target_id"]
                    for target in targets
                )
            ):
                return doc
        return None

    def _find_block(self, query: dict) -> object | None:
        clauses = query["$or"]
        blocker = clauses[0]["blocker_id"]
        blocked = clauses[0]["blocked_id"]["$in"]
        for peer in blocked:
            if (blocker, peer) in self.blocked_pairs or (
                peer,
                blocker,
            ) in self.blocked_pairs:
                return object()
        return None

    def service(self) -> AuthorizationService:
        ownership = OwnershipResolver()
        ownership._resources.update(self.resources)

        roles_repo = AsyncMock()
        roles_repo.list_by_ids.side_effect = lambda ids: [
            self.roles[i] for i in ids if i in self.roles
        ]
        return AuthorizationService(ownership=ownership, roles_repo=roles_repo)

    async def can(self, user_id: str, action: str, resource_type: str, resource_id: str, **kwargs):
        service = self.service()
        now = datetime.now(UTC)

        async def fake_user_get(oid):
            return (
                UserDocument(
                    email="u@example.com",
                    username="u",
                    created_at=now,
                    updated_at=now,
                )
                if str(oid) in self.known_users
                else None
            )

        with (
            patch.object(UserDocument, "get", new=AsyncMock(side_effect=fake_user_get)),
            patch(
                "app.modules.authorization.service.RelationshipDocument.find_one",
                new=AsyncMock(side_effect=lambda q: self._find_relationship(q)),
            ),
            patch(
                "app.modules.authorization.service.BlockDocument.find_one",
                new=AsyncMock(side_effect=lambda q: self._find_block(q)),
            ),
        ):
            return await service.can(
                user_id, action, resource_type, resource_id, **kwargs
            )


@pytest.fixture
def fx() -> _Fixture:
    return _Fixture()


# --- 5.1 resolver matrix -----------------------------------------------------


@pytest.mark.asyncio
async def test_unknown_user_is_denied(fx):
    """Step 1: platform status. A user we cannot load has no standing."""
    fx.add_resource("channel", CHANNEL_ID, make_channel())
    fx.known_users = set()

    assert await fx.can(STRANGER, RESOURCE_VIEW, "channel", CHANNEL_ID) is False


@pytest.mark.asyncio
async def test_missing_resource_is_denied(fx):
    assert await fx.can(OWNER, RESOURCE_VIEW, "channel", CHANNEL_ID) is False


@pytest.mark.asyncio
async def test_block_denies_interaction_but_not_reads(fx):
    """Step 2: a block suspends interaction in a DM; visibility survives."""
    fx.add_resource("conversation", DM_ID, make_dm())
    fx.add_membership(
        make_membership(user_id=MEMBER, target_type="conversation", target_id=DM_ID)
    )
    fx.blocked_pairs.add((PEER, MEMBER))

    assert await fx.can(MEMBER, MESSAGE_CREATE, "conversation", DM_ID) is False
    assert await fx.can(MEMBER, MESSAGE_READ, "conversation", DM_ID) is True


@pytest.mark.asyncio
async def test_active_membership_role_grants(fx):
    """Step 7: an active membership's roles grant their permissions."""
    fx.add_resource("channel", CHANNEL_ID, make_channel())
    fx.add_role(make_role(ROLE_MEMBER_ID, ROLE_MEMBER, {MESSAGE_CREATE, MESSAGE_READ}))
    fx.add_membership(
        make_membership(
            user_id=MEMBER,
            target_type="channel",
            target_id=CHANNEL_ID,
            role_ids=[ROLE_MEMBER_ID],
        )
    )

    assert await fx.can(MEMBER, MESSAGE_CREATE, "channel", CHANNEL_ID) is True


@pytest.mark.asyncio
async def test_pending_membership_grants_no_role_permissions(fx):
    """Step 4 / §58: only an `active` membership resolves roles."""
    fx.add_resource("channel", CHANNEL_ID, make_channel(visibility="private"))
    fx.add_role(make_role(ROLE_MEMBER_ID, ROLE_MEMBER, {MESSAGE_CREATE}))
    fx.add_membership(
        make_membership(
            user_id=MEMBER,
            target_type="channel",
            target_id=CHANNEL_ID,
            status="pending",
            role_ids=[ROLE_MEMBER_ID],
        )
    )

    assert await fx.can(MEMBER, MESSAGE_CREATE, "channel", CHANNEL_ID) is False


@pytest.mark.parametrize("status", ["pending", "declined", "revoked"])
@pytest.mark.asyncio
async def test_inactive_membership_statuses_grant_nothing(fx, status):
    fx.add_resource("channel", CHANNEL_ID, make_channel(visibility="private"))
    fx.add_role(make_role(ROLE_MEMBER_ID, ROLE_MEMBER, {RESOURCE_VIEW}))
    fx.add_membership(
        make_membership(
            user_id=MEMBER,
            target_type="channel",
            target_id=CHANNEL_ID,
            status=status,
            role_ids=[ROLE_MEMBER_ID],
        )
    )

    assert await fx.can(MEMBER, RESOURCE_VIEW, "channel", CHANNEL_ID) is False


@pytest.mark.asyncio
async def test_explicit_deny_beats_an_allowing_role(fx):
    """Step 5 / §55: `deny` outranks every grant below it."""
    fx.add_resource("channel", CHANNEL_ID, make_channel())
    fx.add_role(make_role(ROLE_MOD_ID, ROLE_MODERATOR, {MESSAGE_DELETE_ANY}))
    fx.add_membership(
        make_membership(
            user_id=MEMBER,
            target_type="channel",
            target_id=CHANNEL_ID,
            role_ids=[ROLE_MOD_ID],
            deny=[MESSAGE_DELETE_ANY],
        )
    )

    assert await fx.can(MEMBER, MESSAGE_DELETE_ANY, "channel", CHANNEL_ID) is False


@pytest.mark.asyncio
async def test_explicit_allow_grants_without_a_role(fx):
    """Step 6: an allow override stands in for a role."""
    fx.add_resource("channel", CHANNEL_ID, make_channel())
    fx.add_membership(
        make_membership(
            user_id=MEMBER,
            target_type="channel",
            target_id=CHANNEL_ID,
            allow=[MEMBER_INVITE],
        )
    )

    assert await fx.can(MEMBER, MEMBER_INVITE, "channel", CHANNEL_ID) is True


@pytest.mark.asyncio
async def test_parent_space_role_manages_child_channel(fx):
    """Step 8 / §53: `channel.manage` on the space is `resource.manage` on its channels."""
    fx.add_resource("space", SPACE_ID, make_space())
    fx.add_resource(
        "channel",
        CHANNEL_ID,
        make_channel(owner=OwnerRef(type="space", id=SPACE_ID), space_id=SPACE_ID),
    )
    fx.add_role(
        make_role(
            ROLE_SPACE_ADMIN_ID,
            ROLE_ADMIN,
            {CHANNEL_MANAGE},
            scope_type="space",
            scope_id=SPACE_ID,
        )
    )
    fx.add_membership(
        make_membership(
            user_id=MEMBER,
            target_type="space",
            target_id=SPACE_ID,
            role_ids=[ROLE_SPACE_ADMIN_ID],
        )
    )

    assert await fx.can(MEMBER, RESOURCE_MANAGE, "channel", CHANNEL_ID) is True
    # A user with no space membership gets nothing from the parent scope.
    assert await fx.can(STRANGER, RESOURCE_MANAGE, "channel", CHANNEL_ID) is False


@pytest.mark.asyncio
async def test_public_visibility_grants_view_without_membership(fx):
    """Step 10: a public resource is viewable by any authenticated user."""
    fx.add_resource("channel", CHANNEL_ID, make_channel(visibility="public"))

    assert await fx.can(STRANGER, RESOURCE_VIEW, "channel", CHANNEL_ID) is True


@pytest.mark.asyncio
async def test_private_channel_denies_non_member_view(fx):
    fx.add_resource("channel", CHANNEL_ID, make_channel(visibility="private"))

    assert await fx.can(STRANGER, RESOURCE_VIEW, "channel", CHANNEL_ID) is False


@pytest.mark.asyncio
async def test_default_deny(fx):
    """Step 11: nothing grants it, so it is denied."""
    fx.add_resource("channel", CHANNEL_ID, make_channel(visibility="public"))
    fx.add_membership(
        make_membership(user_id=MEMBER, target_type="channel", target_id=CHANNEL_ID)
    )

    assert await fx.can(MEMBER, RESOURCE_MANAGE, "channel", CHANNEL_ID) is False


# --- 5.2 ownership -----------------------------------------------------------


@pytest.mark.asyncio
async def test_owner_bypasses_rbac(fx):
    """Step 3 / §51: the owner needs neither membership nor role."""
    fx.add_resource("channel", CHANNEL_ID, make_channel())

    assert await fx.can(OWNER, RESOURCE_MANAGE, "channel", CHANNEL_ID) is True
    assert await fx.can(OWNER, MESSAGE_DELETE_ANY, "channel", CHANNEL_ID) is True


@pytest.mark.asyncio
async def test_space_owner_controls_child_channel_without_membership(fx):
    """§19: effective ownership cascades from a space to its children."""
    fx.add_resource("space", SPACE_ID, make_space())
    fx.add_resource(
        "channel",
        CHANNEL_ID,
        make_channel(owner=OwnerRef(type="space", id=SPACE_ID), space_id=SPACE_ID),
    )

    assert await fx.can(OWNER, RESOURCE_MANAGE, "channel", CHANNEL_ID) is True
    assert await fx.can(STRANGER, RESOURCE_MANAGE, "channel", CHANNEL_ID) is False


@pytest.mark.asyncio
async def test_effective_owner_id_follows_the_space(fx):
    ownership = OwnershipResolver()
    ownership._resources[("space", SPACE_ID)] = make_space()
    ownership._resources[("channel", CHANNEL_ID)] = make_channel(
        owner=OwnerRef(type="space", id=SPACE_ID), space_id=SPACE_ID
    )

    assert (
        await ownership.effective_owner_id(
            resource_type="channel", resource_id=CHANNEL_ID
        )
        == OWNER
    )
    assert await ownership.is_effective_owner(
        user_id=OWNER, resource_type="channel", resource_id=CHANNEL_ID
    )


@pytest.mark.asyncio
async def test_dm_has_no_owner(fx):
    """A DM is ownerless, so nobody bypasses RBAC in one."""
    ownership = OwnershipResolver()
    ownership._resources[("conversation", DM_ID)] = make_dm()

    assert (
        await ownership.effective_owner_id(
            resource_type="conversation", resource_id=DM_ID
        )
        is None
    )


# --- 5.3 policy vs RBAC ------------------------------------------------------


@pytest.mark.asyncio
async def test_posting_policy_owner_denies_a_member(fx):
    """§59: policy limits who may post even when RBAC would allow it."""
    fx.add_resource("channel", CHANNEL_ID, make_channel(posting_policy="owner"))
    fx.add_role(make_role(ROLE_MEMBER_ID, ROLE_MEMBER, {MESSAGE_CREATE}))
    fx.add_membership(
        make_membership(
            user_id=MEMBER,
            target_type="channel",
            target_id=CHANNEL_ID,
            role_ids=[ROLE_MEMBER_ID],
        )
    )

    assert await fx.can(MEMBER, MESSAGE_CREATE, "channel", CHANNEL_ID) is False
    # The owner still posts — ownership is resolved before policy.
    assert await fx.can(OWNER, MESSAGE_CREATE, "channel", CHANNEL_ID) is True


@pytest.mark.asyncio
async def test_posting_policy_moderators_admits_a_moderator(fx):
    fx.add_resource("channel", CHANNEL_ID, make_channel(posting_policy="moderators"))
    fx.add_role(make_role(ROLE_MEMBER_ID, ROLE_MEMBER, {MESSAGE_CREATE}))
    fx.add_role(
        make_role(ROLE_MOD_ID, ROLE_MODERATOR, {MESSAGE_CREATE, MESSAGE_DELETE_ANY})
    )
    fx.add_membership(
        make_membership(
            user_id=MEMBER,
            target_type="channel",
            target_id=CHANNEL_ID,
            role_ids=[ROLE_MEMBER_ID],
        )
    )
    fx.add_membership(
        make_membership(
            user_id=STRANGER,
            target_type="channel",
            target_id=CHANNEL_ID,
            role_ids=[ROLE_MOD_ID],
        )
    )

    assert await fx.can(MEMBER, MESSAGE_CREATE, "channel", CHANNEL_ID) is False
    assert await fx.can(STRANGER, MESSAGE_CREATE, "channel", CHANNEL_ID) is True


@pytest.mark.asyncio
async def test_comment_policy_disabled_blocks_reactions(fx):
    fx.add_resource("channel", CHANNEL_ID, make_channel(comment_policy="disabled"))
    fx.add_role(make_role(ROLE_MOD_ID, ROLE_MODERATOR, {"reaction.create"}))
    fx.add_membership(
        make_membership(
            user_id=MEMBER,
            target_type="channel",
            target_id=CHANNEL_ID,
            role_ids=[ROLE_MOD_ID],
        )
    )

    assert await fx.can(MEMBER, "reaction.create", "channel", CHANNEL_ID) is False


@pytest.mark.asyncio
async def test_comment_policy_followers_requires_active_follow(fx):
    fx.add_resource(
        "channel",
        CHANNEL_ID,
        make_channel(comment_policy="followers", posting_policy="owner"),
    )
    fx.add_follow(
        make_follow(
            user_id=MEMBER,
            target_type="channel",
            target_id=CHANNEL_ID,
        )
    )

    assert await fx.can(MEMBER, "thread.reply", "channel", CHANNEL_ID) is True
    assert await fx.can(STRANGER, "thread.reply", "channel", CHANNEL_ID) is False


@pytest.mark.asyncio
async def test_private_profile_read_accepts_approved_user_follower(fx):
    fx.add_resource(
        "channel",
        CHANNEL_ID,
        make_channel(kind="profile", visibility="members"),
    )
    fx.add_follow(
        make_follow(
            user_id=MEMBER,
            target_type="user",
            target_id=OWNER,
        )
    )

    assert await fx.can(MEMBER, MESSAGE_READ, "channel", CHANNEL_ID) is True
    assert await fx.can(MEMBER, "thread.reply", "channel", CHANNEL_ID) is True
    assert await fx.can(STRANGER, MESSAGE_READ, "channel", CHANNEL_ID) is False


@pytest.mark.asyncio
async def test_join_policy_closed_blocks_invites(fx):
    fx.add_resource("channel", CHANNEL_ID, make_channel(join_policy="closed"))
    fx.add_role(make_role(ROLE_MOD_ID, ROLE_MODERATOR, {MEMBER_INVITE}))
    fx.add_membership(
        make_membership(
            user_id=MEMBER,
            target_type="channel",
            target_id=CHANNEL_ID,
            role_ids=[ROLE_MOD_ID],
        )
    )

    assert await fx.can(MEMBER, MEMBER_INVITE, "channel", CHANNEL_ID) is False


@pytest.mark.asyncio
async def test_own_message_edit_resolves_by_sender(fx):
    """§59: `*.own` is authorship plus the permission."""
    fx.add_resource("channel", CHANNEL_ID, make_channel())
    fx.add_role(make_role(ROLE_MEMBER_ID, ROLE_MEMBER, {MESSAGE_EDIT_OWN}))
    fx.add_membership(
        make_membership(
            user_id=MEMBER,
            target_type="channel",
            target_id=CHANNEL_ID,
            role_ids=[ROLE_MEMBER_ID],
        )
    )

    assert (
        await fx.can(
            MEMBER, MESSAGE_EDIT_OWN, "channel", CHANNEL_ID, sender_id=MEMBER
        )
        is True
    )
    # Someone else's message is not an `*.own` action at all.
    assert (
        await fx.can(
            MEMBER, MESSAGE_EDIT_OWN, "channel", CHANNEL_ID, sender_id=STRANGER
        )
        is False
    )
    assert await fx.can(MEMBER, MESSAGE_EDIT_OWN, "channel", CHANNEL_ID) is False


@pytest.mark.asyncio
async def test_delete_any_satisfies_an_own_delete(fx):
    """Holding the unrestricted form covers the own-scoped request."""
    fx.add_resource("channel", CHANNEL_ID, make_channel())
    fx.add_role(make_role(ROLE_MOD_ID, ROLE_MODERATOR, {MESSAGE_DELETE_ANY}))
    fx.add_membership(
        make_membership(
            user_id=MEMBER,
            target_type="channel",
            target_id=CHANNEL_ID,
            role_ids=[ROLE_MOD_ID],
        )
    )

    assert (
        await fx.can(
            MEMBER, MESSAGE_DELETE_OWN, "channel", CHANNEL_ID, sender_id=MEMBER
        )
        is True
    )


# --- roles + vocabulary ------------------------------------------------------


def test_default_system_roles_cover_the_four_names():
    names = [name for name, _, _ in DEFAULT_SYSTEM_ROLES]
    assert names == [ROLE_ADMIN, ROLE_MODERATOR, ROLE_MEMBER, ROLE_GUEST]
    # Owner is a bypass, never a seeded role (§49, §51).
    assert "Owner" not in names


def test_role_permissions_are_strings_from_the_vocabulary():
    from app.modules.authorization.permissions import PERMISSIONS

    for _, _, permissions in DEFAULT_SYSTEM_ROLES:
        assert permissions <= PERMISSIONS


def test_role_scope_uniqueness_is_an_index():
    by_name = {ix.document["name"]: ix.document for ix in RoleDocument.Settings.indexes}
    unique = by_name["ux_roles_scope_name"]
    assert unique["unique"] is True
    assert list(unique["key"].keys()) == ["scope_type", "scope_id", "name"]
    assert "ix_roles_scope" in by_name


@pytest.mark.asyncio
async def test_create_role_rejects_unknown_permissions():
    from app.core.errors import AppError

    service = RoleService(repo=AsyncMock())
    with pytest.raises(AppError) as exc:
        await service.create_role(
            scope_type="channel",
            scope_id=CHANNEL_ID,
            name="Custom",
            permissions=["message.create", "message.teleport"],
        )
    assert "message.teleport" in exc.value.message


@pytest.mark.asyncio
async def test_seed_default_roles_is_scope_aware():
    repo = AsyncMock()
    seeded: dict[str, list[str]] = {}

    async def capture(**kwargs):
        seeded[kwargs["name"]] = kwargs["permissions"]
        return make_role(ROLE_MEMBER_ID, kwargs["name"], set(kwargs["permissions"]))

    repo.upsert_system_role.side_effect = capture

    await RoleService(repo=repo).seed_default_roles(
        scope_type="conversation", scope_id=CONVERSATION_ID
    )

    assert set(seeded) == {ROLE_ADMIN, ROLE_MODERATOR, ROLE_MEMBER, ROLE_GUEST}
    # Creating channels/groups is a space-scoped power.
    assert CHANNEL_MANAGE not in seeded[ROLE_ADMIN]
