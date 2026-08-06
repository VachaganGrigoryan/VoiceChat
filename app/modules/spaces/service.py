from __future__ import annotations

import secrets
from datetime import UTC, datetime
from typing import Any, Literal

from app.core.errors import AppError
from app.db.models import (
    ChannelDocument,
    ConversationDocument,
    SpaceDocument,
    InviteLinkDocument,
    JoinRequestDocument,
    RelationshipDocument,
)
from app.db.object_id import parse_object_id
from app.modules.authorization import AuthorizationService
from app.modules.authorization.permissions import (
    CHANNEL_CREATE,
    GROUP_CREATE,
    MEMBER_APPROVE,
    MEMBER_INVITE,
    RESOURCE_DELETE,
    RESOURCE_MANAGE,
    RESOURCE_VIEW,
)
from app.modules.authorization.roles import ROLE_ADMIN, ROLE_MEMBER
from app.modules.relationships.repository import RelationshipsRepository
from app.modules.relationships.service import RelationshipService
from app.modules.spaces.repository import SpacesRepository
from app.modules.spaces.schemas import (
    SpaceView,
    SpaceInviteLinkView,
    SpaceJoinRequestView,
    SpaceMemberUserSummary,
    SpaceMemberView,
    SpaceChannelView,
    SpaceGroupView,
)

#: The reserved system space. It is created on demand, sorted first in
#: listings, and cannot be deleted.
DEFAULT_SPACE_SLUG = "vogi"


class SpacesService:
    def __init__(
        self,
        repo: SpacesRepository,
        notifications_service: Any | None = None,
        authorization: AuthorizationService | None = None,
        relationships: RelationshipsRepository | None = None,
    ) -> None:
        self.repo = repo
        self.notifications_service = notifications_service
        self.authorization = authorization or AuthorizationService()
        self.relationships = relationships or RelationshipsRepository()

    async def create_space(
        self,
        *,
        created_by: str,
        name: str,
        slug: str,
        kind: Literal["workspace", "community"] = "workspace",
        visibility: Literal["private", "public"] = "private",
        join_policy: Literal["open", "approval", "invite_only", "closed"] = "open",
        avatar: dict[str, Any] | None = None,
        settings: dict[str, Any] | None = None,
    ) -> SpaceView:
        # Check slug uniqueness
        existing = await self.repo.get_by_slug(slug)
        if existing is not None:
            raise AppError(
                code="SPACE_SLUG_TAKEN",
                message="A space with this slug already exists",
                status_code=409,
            )

        now = datetime.now(UTC)
        st_dict = dict(settings or {})
        st_dict["kind"] = kind
        space = SpaceDocument(
            name=name.strip(),
            slug=slug.strip().lower(),
            owner_user_id=str(created_by),
            created_by=str(created_by),
            avatar=avatar,
            visibility=visibility,
            join_policy=join_policy,
            settings=st_dict,
            created_at=now,
            updated_at=now,
        )
        await space.insert()

        # The creator owns the space via `owner_user_id`; Admin is the role
        # that outlives an ownership change (§51).
        seeded = await self.repo.seed_roles(space_id=space.str_id)
        # What a joiner gets when no role is named. Without this a new member
        # holds an active membership with no permissions, and nothing inherits
        # to the space's channels or groups (§53, §63).
        member_role = seeded.get(ROLE_MEMBER)
        if member_role is not None:
            space.default_role_ids = [member_role.str_id]
            await space.save()
        await self.repo.ensure_membership(
            space_id=space.str_id,
            user_id=created_by,
            role=ROLE_ADMIN,
        )

        from app.db.models import AuditLogDocument

        log = AuditLogDocument(
            actor_id=created_by,
            action="create_space",
            target_type="space",
            target_id=space.str_id,
            space_id=space.str_id,
            data={},
        )
        await log.insert()

        return self._to_view(space, viewer_role=ROLE_ADMIN)

    async def get_space(self, *, space_id: str, user_id: str) -> SpaceView:
        space = await self.repo.get_by_id(space_id)
        if space is None:
            raise AppError(
                code="SPACE_NOT_FOUND",
                message="Space not found",
                status_code=404,
            )

        # Enforce membership for private spaces
        membership = await self.repo.get_membership(space_id=space_id, user_id=user_id)
        if space.visibility == "private":
            if membership is None:
                raise AppError(
                    code="SPACE_FORBIDDEN",
                    message="Not a member of this space",
                    status_code=403,
                )

        viewer_role = membership.role if membership is not None else None
        return self._to_view(space, viewer_role=viewer_role)

    async def ensure_default_vogi_space(
        self, user_id: str | None = None
    ) -> SpaceDocument:
        """Find or create the default Vogi system space, ensuring user_id is a member if provided."""
        vogi_space = await self.repo.get_by_slug(DEFAULT_SPACE_SLUG)
        if vogi_space is None:
            now = datetime.now(UTC)
            vogi_space = SpaceDocument(
                name="Vogi",
                slug=DEFAULT_SPACE_SLUG,
                owner_user_id=user_id or "system",
                created_by=user_id or "system",
                visibility="public",
                join_policy="open",
                settings={"kind": "community", "is_default": True},
                created_at=now,
                updated_at=now,
            )
            await vogi_space.insert()
            seeded = await self.repo.seed_roles(space_id=vogi_space.str_id)
            member_role = seeded.get(ROLE_MEMBER)
            if member_role is not None:
                vogi_space.default_role_ids = [member_role.str_id]
                await vogi_space.save()

        if user_id:
            m = await self.repo.get_membership(
                space_id=vogi_space.str_id, user_id=user_id
            )
            if m is None:
                await self.repo.ensure_membership(
                    space_id=vogi_space.str_id,
                    user_id=user_id,
                    role=ROLE_MEMBER,
                )
        return vogi_space

    async def list_for_user(self, *, user_id: str) -> list[SpaceView]:
        await self.ensure_default_vogi_space(user_id=user_id)
        memberships = await self.repo.list_memberships_for_user(user_id=user_id)
        spaces: list[SpaceView] = []
        for member in memberships:
            space = await self.repo.get_by_id(str(member.space_id))
            if space is not None:
                spaces.append(self._to_view(space, viewer_role=member.role))
        spaces.sort(
            key=lambda s: 0 if (s.slug == DEFAULT_SPACE_SLUG or s.is_default) else 1
        )
        return spaces

    async def check_membership(self, *, space_id: str, user_id: str) -> bool:
        membership = await self.repo.get_membership(space_id=space_id, user_id=user_id)
        return membership is not None

    async def _require_can(
        self, *, space_id: str, user_id: str, permission: str
    ) -> SpaceDocument:
        """Authorize a space action through `AuthorizationService.can` (§56).

        The space owner passes on effective ownership; everyone else needs a
        role on their active space membership that grants ``permission``.
        """
        space = await self.repo.get_by_id(space_id)
        if space is None:
            raise AppError(
                code="SPACE_NOT_FOUND",
                message="Space not found",
                status_code=404,
            )
        allowed = await self.authorization.can(
            user_id, permission, "space", space.str_id
        )
        if not allowed:
            raise AppError(
                code="SPACE_FORBIDDEN",
                message="Not allowed to manage this space",
                status_code=403,
            )
        return space

    async def create_invite(
        self,
        *,
        actor_user_id: str,
        space_id: str,
        expires_at: datetime | None = None,
        max_uses: int | None = None,
        approval_required: bool = False,
        role_ids: list[str] | None = None,
    ) -> SpaceInviteLinkView:
        await self._require_can(
            space_id=space_id, user_id=actor_user_id, permission=MEMBER_INVITE
        )
        invite = await self.repo.create_invite_link(
            target_id=space_id,
            created_by=actor_user_id,
            code=secrets.token_urlsafe(12),
            expires_at=expires_at,
            max_uses=max_uses,
            approval_required=approval_required,
            role_ids=role_ids or [],
        )
        return self._to_invite_view(invite)

    async def invite_user(
        self,
        *,
        actor_user_id: str,
        space_id: str,
        user_id: str,
    ) -> SpaceInviteLinkView:
        await self._require_can(
            space_id=space_id, user_id=actor_user_id, permission=MEMBER_INVITE
        )

        space = await self.repo.get_by_id(space_id)
        if space is None:
            raise AppError(
                code="SPACE_NOT_FOUND",
                message="Space not found",
                status_code=404,
            )

        # Check if user is already a member
        existing = await self.repo.get_membership(space_id=space_id, user_id=user_id)
        if existing is not None:
            raise AppError(
                code="ALREADY_MEMBER",
                message="User is already a member of this space",
                status_code=409,
            )

        invite = await self.repo.create_invite_link(
            target_id=space_id,
            created_by=actor_user_id,
            code=secrets.token_urlsafe(12),
            expires_at=None,
            max_uses=1,
            approval_required=False,
            role_ids=[],
            invitee_id=user_id,
        )

        if self.notifications_service is not None:
            await self.notifications_service.create_notification(
                user_id=user_id,
                kind="membership_invite",
                actor_user_id=actor_user_id,
                resource_type="space",
                resource_id=space_id,
                data={
                    "space_id": space_id,
                    "space_name": space.name,
                    "invited_by": actor_user_id,
                    "code": invite.code,
                },
            )

        return self._to_invite_view(invite)

    async def list_invites(
        self, *, actor_user_id: str, space_id: str
    ) -> list[SpaceInviteLinkView]:
        await self._require_can(
            space_id=space_id, user_id=actor_user_id, permission=MEMBER_INVITE
        )
        invites = await self.repo.list_invites_for_space(space_id=space_id)
        return [self._to_invite_view(inv) for inv in invites]

    async def revoke_invite(
        self, *, actor_user_id: str, space_id: str, invite_id: str
    ) -> None:
        await self._require_can(
            space_id=space_id, user_id=actor_user_id, permission=MEMBER_INVITE
        )
        invite = await self.repo.get_invite_by_id(invite_id=invite_id)
        if invite is None or str(invite.target_id) != space_id:
            raise AppError(
                code="INVITE_NOT_FOUND",
                message="Invite link not found",
                status_code=404,
            )
        await self.repo.revoke_invite(invite_id=invite_id)

    async def redeem_invite(
        self, *, user_id: str, code: str
    ) -> tuple[Literal["joined", "pending"], SpaceView | None, RelationshipDocument]:
        invite = await self.repo.get_invite_by_code(code)
        if invite is None:
            raise AppError(
                code="INVITE_NOT_FOUND",
                message="Invite link not found",
                status_code=404,
            )

        if getattr(invite, "invitee_id", None) is not None and str(
            invite.invitee_id
        ) != str(user_id):
            raise AppError(
                code="INVITE_FORBIDDEN",
                message="This invite is not intended for you",
                status_code=403,
            )

        space = await self.repo.get_by_id(str(invite.target_id))
        if space is None:
            raise AppError(
                code="SPACE_NOT_FOUND",
                message="Space not found",
                status_code=404,
            )

        existing = await self.relationships.find_edge(
            kind="membership",
            user_id=user_id,
            target_type="space",
            target_id=space.str_id,
        )
        if existing is not None and existing.status == "active":
            return "joined", self._to_view(space), existing
        if existing is not None and existing.status == "pending":
            return "pending", None, existing

        consumed = await self.repo.consume_invite_use(code=code)
        if consumed is None:
            raise AppError(
                code="INVITE_UNAVAILABLE",
                message="Invite link is revoked, expired, or fully used",
                status_code=410,
            )

        if invite.approval_required:
            pending = await RelationshipService(repo=self.relationships).request(
                kind="membership",
                user_id=user_id,
                target_type="space",
                target_id=space.str_id,
                status="pending",
                role_ids=[str(role_id) for role_id in invite.role_ids],
            )
            return "pending", None, pending

        await self.repo.ensure_membership(
            space_id=space.str_id,
            user_id=user_id,
            role=ROLE_MEMBER,
        )
        membership = await self.relationships.find_edge(
            kind="membership",
            user_id=user_id,
            target_type="space",
            target_id=space.str_id,
        )
        if membership is None:  # pragma: no cover - membership write just succeeded
            raise AppError(
                code="MEMBERSHIP_NOT_FOUND",
                message="Membership was not created",
                status_code=500,
            )
        if invite.role_ids:
            membership.role_ids = list(invite.role_ids)
            await membership.save()
        return "joined", self._to_view(space), membership

    async def list_join_requests(
        self, *, actor_user_id: str, space_id: str
    ) -> list[SpaceJoinRequestView]:
        await self._require_can(
            space_id=space_id, user_id=actor_user_id, permission=MEMBER_APPROVE
        )
        requests = await self.repo.list_pending_join_requests(space_id=space_id)
        return [self._to_join_request_view(req) for req in requests]

    async def approve_join_request(
        self, *, actor_user_id: str, space_id: str, request_id: str
    ) -> SpaceJoinRequestView:
        await self._require_can(
            space_id=space_id, user_id=actor_user_id, permission=MEMBER_APPROVE
        )
        request = await self.repo.get_join_request(request_id=request_id)
        if request is None or str(request.target_id) != space_id:
            raise AppError(
                code="JOIN_REQUEST_NOT_FOUND",
                message="Join request not found",
                status_code=404,
            )

        updated = await self.repo.set_join_request_status(
            request_id=request_id, status="approved"
        )
        if updated is None:
            raise AppError(
                code="JOIN_REQUEST_RESOLVED",
                message="Join request has already been resolved",
                status_code=409,
            )

        await self.repo.ensure_membership(
            space_id=space_id,
            user_id=str(request.user_id),
            role=ROLE_MEMBER,
        )

        from app.db.models import AuditLogDocument

        log = AuditLogDocument(
            actor_id=actor_user_id,
            action="approve_join_request",
            target_type="space",
            target_id=space_id,
            space_id=space_id,
            data={"user_id": str(request.user_id), "request_id": request_id},
        )
        await log.insert()

        return self._to_join_request_view(updated)

    async def reject_join_request(
        self, *, actor_user_id: str, space_id: str, request_id: str
    ) -> SpaceJoinRequestView:
        await self._require_can(
            space_id=space_id, user_id=actor_user_id, permission=MEMBER_APPROVE
        )
        request = await self.repo.get_join_request(request_id=request_id)
        if request is None or str(request.target_id) != space_id:
            raise AppError(
                code="JOIN_REQUEST_NOT_FOUND",
                message="Join request not found",
                status_code=404,
            )

        updated = await self.repo.set_join_request_status(
            request_id=request_id, status="rejected"
        )
        if updated is None:
            raise AppError(
                code="JOIN_REQUEST_RESOLVED",
                message="Join request has already been resolved",
                status_code=409,
            )

        from app.db.models import AuditLogDocument

        log = AuditLogDocument(
            actor_id=actor_user_id,
            action="reject_join_request",
            target_type="space",
            target_id=space_id,
            space_id=space_id,
            data={"user_id": str(request.user_id), "request_id": request_id},
        )
        await log.insert()

        return self._to_join_request_view(updated)

    async def request_join(
        self, *, user_id: str, space_id: str
    ) -> SpaceJoinRequestView:
        """Create a JoinRequest directly (ping-to-org)."""
        space = await self.repo.get_by_id(space_id)
        if space is None:
            raise AppError(
                code="SPACE_NOT_FOUND",
                message="Space not found",
                status_code=404,
            )

        # Check existing membership
        existing = await self.repo.get_membership(space_id=space_id, user_id=user_id)
        if existing is not None:
            raise AppError(
                code="ALREADY_MEMBER",
                message="You are already a member of this space",
                status_code=409,
            )

        # Create or return existing pending join request
        pending = await self.repo.get_pending_join_request(
            space_id=space_id, user_id=user_id
        )
        if pending is None:
            pending = await self.repo.create_join_request(
                space_id=space_id, user_id=user_id
            )
        return self._to_join_request_view(pending)

    def _to_view(
        self, space: SpaceDocument, viewer_role: str | None = None
    ) -> SpaceView:
        is_default = space.slug == DEFAULT_SPACE_SLUG or bool(
            space.settings and space.settings.get("is_default")
        )
        return SpaceView(
            id=space.str_id,
            name=space.name,
            slug=space.slug,
            kind=space.kind,
            owner_user_id=str(space.owner_user_id),
            created_by=str(space.created_by),
            avatar=space.avatar,
            visibility=space.visibility,
            join_policy=space.join_policy,
            settings=space.settings,
            created_at=space.created_at,
            updated_at=space.updated_at,
            viewer_role=viewer_role,
            is_default=is_default,
        )

    async def _require_space_member(self, *, space_id: str, user_id: str) -> None:
        if not await self.check_membership(space_id=space_id, user_id=user_id):
            raise AppError(
                code="SPACE_FORBIDDEN",
                message="Not a member of this space",
                status_code=403,
            )

    async def list_channels(
        self, *, space_id: str, user_id: str
    ) -> list[SpaceChannelView]:
        """Space channels the caller may read (§63).

        Visibility is decided per channel by `AuthorizationService.can`, which
        already encodes the rules this change needs: a `members` channel is
        readable on space membership alone through parent-scope inheritance,
        while a `private` one is capped until an explicit channel membership
        exists. Filtering here rather than in the query keeps one source of
        truth for access.
        """
        await self._require_space_member(space_id=space_id, user_id=user_id)

        # `space_id` is a `StrId`, stored as a string — an ObjectId here never
        # matches, which is what left the previous implementation dead.
        channels = await ChannelDocument.find({"space_id": str(space_id)}).to_list()

        views: list[SpaceChannelView] = []
        for channel in channels:
            if not await self.authorization.can(
                user_id, RESOURCE_VIEW, "channel", channel.str_id
            ):
                continue
            membership = await self.relationships.find_edge(
                kind="membership",
                user_id=str(user_id),
                target_type="channel",
                target_id=channel.str_id,
            )
            views.append(
                SpaceChannelView(
                    id=channel.str_id,
                    name=channel.name,
                    slug=channel.slug,
                    description=channel.description,
                    kind=channel.kind,
                    visibility=channel.visibility,
                    posting_policy=channel.posting_policy,
                    joined=membership is not None and membership.status == "active",
                )
            )
        return views

    async def create_channel(
        self,
        *,
        space_id: str,
        user_id: str,
        channel_service: Any,
        name: str,
        slug: str,
        description: str | None = None,
        kind: Literal["text", "announcement"] = "text",
        visibility: Literal["public", "members", "private"] = "members",
        posting_policy: Literal[
            "owner", "moderators", "members", "everyone"
        ] = "members",
        comment_policy: Literal[
            "disabled", "followers", "members", "everyone"
        ] = "members",
        tags: list[str] | None = None,
    ) -> Any:
        """Create a space-owned channel (§21, §24).

        `channel.create` is a space-scoped permission, so the gate is on the
        space; the resulting channel carries `owner={type:space}` and
        `space_id`, which is what makes role inheritance resolve later.

        `join_policy` is derived from `visibility` rather than defaulted: a
        `private` channel that stayed self-joinable would let any space member
        create their own membership, and membership is exactly what grants read
        on a private channel (§63) — the isolation would be decorative.
        """
        space = await self._require_can(
            space_id=space_id, user_id=user_id, permission=CHANNEL_CREATE
        )
        join_policy = "invite_only" if visibility == "private" else "open"
        return await channel_service.create(
            created_by=str(user_id),
            name=name,
            slug=slug,
            kind=kind,
            description=description,
            visibility=visibility,
            join_policy=join_policy,
            posting_policy=posting_policy,
            comment_policy=comment_policy,
            tags=tags or [],
            owner_type="space",
            owner_id=space.str_id,
            space_id=space.str_id,
        )

    async def list_groups(self, *, space_id: str, user_id: str) -> list[SpaceGroupView]:
        """Space-owned groups (§25).

        Unlike channels, participation is never implied by space membership, so
        this lists the space's groups and reports `joined` per group rather than
        hiding the ones the caller has not joined.
        """
        await self._require_space_member(space_id=space_id, user_id=user_id)

        groups = await ConversationDocument.find(
            {"space_id": str(space_id), "type": "group"}
        ).to_list()

        return [
            SpaceGroupView(
                id=group.str_id,
                title=group.title,
                participant_count=len(group.participant_ids),
                joined=str(user_id) in {str(pid) for pid in group.participant_ids},
                created_at=group.created_at,
            )
            for group in groups
        ]

    async def create_group(
        self,
        *,
        space_id: str,
        user_id: str,
        conversations_service: Any,
        title: str,
        participant_ids: list[str],
    ) -> SpaceGroupView:
        """Create a space-owned group (§21, §25)."""
        space = await self._require_can(
            space_id=space_id, user_id=user_id, permission=GROUP_CREATE
        )
        conversation = await conversations_service.create_group_conversation(
            user_id=str(user_id),
            title=title,
            participant_ids=participant_ids,
            space_id=space.str_id,
        )
        return SpaceGroupView(
            id=conversation.str_id,
            title=conversation.title,
            participant_count=len(conversation.participant_ids),
            joined=True,
            created_at=conversation.created_at,
        )

    async def join_channel(
        self, *, space_id: str, channel_id: str, user_id: str
    ) -> None:
        """Join a space channel explicitly (§63).

        Only `open` channels are self-joinable; `private` ones require an invite
        or approval, which is the membership flow rather than this one.
        """
        await self._require_space_member(space_id=space_id, user_id=user_id)

        channel = await ChannelDocument.get(parse_object_id(channel_id))
        if channel is None or str(channel.space_id or "") != str(space_id):
            raise AppError(
                code="CHANNEL_NOT_FOUND",
                message="Channel not found in this space",
                status_code=404,
            )

        if channel.join_policy != "open":
            raise AppError(
                code="CHANNEL_JOIN_FORBIDDEN",
                message="This channel is not open to self-join",
                status_code=403,
            )

        # No role_ids: channels seed no scope roles, and read for a space
        # channel resolves through space-role inheritance. The membership row
        # itself is what `private` visibility checks for (§63).
        await self.relationships.upsert_membership(
            user_id=str(user_id),
            target_type="channel",
            target_id=channel.str_id,
            initiated_by=str(user_id),
        )

    async def list_members(
        self, *, space_id: str, user_id: str
    ) -> list[SpaceMemberView]:
        is_member = await self.check_membership(space_id=space_id, user_id=user_id)
        if not is_member:
            raise AppError(
                code="SPACE_FORBIDDEN",
                message="Not a member of this space",
                status_code=403,
            )

        memberships = await self.repo.list_members_for_space(space_id=space_id)
        if not memberships:
            return []

        user_ids = [str(m.user_id) for m in memberships]
        from app.modules.auth.repository import UsersRepository

        users_map = await UsersRepository().find_by_ids(user_ids)

        return [
            SpaceMemberView(
                id=m.str_id,
                space_id=str(m.space_id),
                user_id=str(m.user_id),
                role=m.role,
                joined_at=m.joined_at,
                user=SpaceMemberUserSummary(
                    id=str(m.user_id),
                    username=users_map[str(m.user_id)].username
                    if str(m.user_id) in users_map
                    else None,
                    display_name=users_map[str(m.user_id)].display_name
                    if str(m.user_id) in users_map
                    else None,
                    avatar=users_map[str(m.user_id)].avatar
                    if str(m.user_id) in users_map
                    else None,
                ),
            )
            for m in memberships
        ]

    async def delete_space(self, *, actor_user_id: str, space_id: str) -> list[str]:
        """Hard-deletes a space and every channel and group it owns.

        Cascading is not optional. A space-owned channel or group resolves its
        owner through the space, so one left behind would resolve no owner and
        could never be managed or deleted again.

        Returns the audience to notify, snapshotted before the cascade because
        it is read from the membership records being deleted.
        """
        space = await self._require_can(
            space_id=space_id, user_id=actor_user_id, permission=RESOURCE_DELETE
        )

        if space.slug == DEFAULT_SPACE_SLUG:
            raise AppError(
                code="SPACE_UNDELETABLE",
                message="The default space cannot be deleted",
                status_code=409,
            )

        from app.modules.authorization.capabilities import affected_viewer_ids
        from app.modules.resources import ResourceCascade

        recipients = await affected_viewer_ids(
            resource_type="space", resource_id=space.str_id
        )
        report = await ResourceCascade().delete_space_tree(space)

        from app.db.models import AuditLogDocument

        await AuditLogDocument(
            actor_id=actor_user_id,
            action="delete_space",
            target_type="space",
            target_id=space.str_id,
            space_id=space.str_id,
            data={"removed": report.removed},
        ).insert()
        return recipients

    async def update_space(
        self,
        *,
        actor_user_id: str,
        space_id: str,
        name: str | None = None,
        visibility: Literal["private", "public"] | None = None,
        settings: dict[str, Any] | None = None,
    ) -> SpaceView:
        await self._require_can(
            space_id=space_id, user_id=actor_user_id, permission=RESOURCE_MANAGE
        )

        updated = await self.repo.update_space(
            space_id=space_id,
            name=name,
            visibility=visibility,
            settings=settings,
        )
        if updated is None:
            raise AppError(
                code="SPACE_NOT_FOUND",
                message="Space not found",
                status_code=404,
            )

        from app.db.models import AuditLogDocument

        log = AuditLogDocument(
            actor_id=actor_user_id,
            action="update_space",
            target_type="space",
            target_id=space_id,
            space_id=space_id,
            data={
                "name": name,
                "visibility": visibility,
                "settings": settings,
            },
        )
        await log.insert()

        membership = await self.repo.get_membership(
            space_id=space_id, user_id=actor_user_id
        )
        viewer_role = membership.role if membership is not None else None
        return self._to_view(updated, viewer_role=viewer_role)

    def _to_invite_view(self, invite: InviteLinkDocument) -> SpaceInviteLinkView:
        return SpaceInviteLinkView(
            id=invite.str_id,
            target_type="space",
            target_id=str(invite.target_id),
            code=invite.code,
            created_by=str(invite.created_by),
            expires_at=invite.expires_at,
            max_uses=invite.max_uses,
            uses=invite.uses,
            approval_required=invite.approval_required,
            role_ids=[str(role_id) for role_id in invite.role_ids],
            revoked=invite.revoked,
            invitee_id=getattr(invite, "invitee_id", None),
        )

    def _to_join_request_view(
        self, request: JoinRequestDocument
    ) -> SpaceJoinRequestView:
        return SpaceJoinRequestView(
            id=request.str_id,
            target_type="space",
            target_id=str(request.target_id),
            user_id=str(request.user_id),
            status=request.status,
            invite_code=request.invite_code,
            created_at=request.created_at,
            responded_at=request.responded_at,
        )
