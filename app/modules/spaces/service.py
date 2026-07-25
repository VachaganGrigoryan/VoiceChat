from __future__ import annotations

import secrets
from datetime import UTC, datetime
from typing import Any, Literal

from app.core.errors import AppError
from app.db.models import (
    SpaceDocument,
    InviteLinkDocument,
    JoinRequestDocument,
)
from app.modules.authorization import AuthorizationService
from app.modules.authorization.permissions import (
    MEMBER_APPROVE,
    MEMBER_INVITE,
    RESOURCE_MANAGE,
)
from app.modules.authorization.roles import ROLE_ADMIN, ROLE_MEMBER
from app.modules.spaces.repository import SpacesRepository
from app.modules.spaces.schemas import (
    SpaceView,
    SpaceInviteLinkView,
    SpaceJoinRequestView,
    SpaceMemberUserSummary,
    SpaceMemberView,
    SpaceChannelView,
)

class SpacesService:
    def __init__(
        self,
        repo: SpacesRepository,
        notifications_service: Any | None = None,
        authorization: AuthorizationService | None = None,
    ) -> None:
        self.repo = repo
        self.notifications_service = notifications_service
        self.authorization = authorization or AuthorizationService()

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
        await self.repo.seed_roles(space_id=space.str_id)
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

    async def list_for_user(self, *, user_id: str) -> list[SpaceView]:
        memberships = await self.repo.list_memberships_for_user(user_id=user_id)
        spaces: list[SpaceView] = []
        for member in memberships:
            space = await self.repo.get_by_id(str(member.space_id))
            if space is not None:
                spaces.append(self._to_view(space, viewer_role=member.role))
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
        requires_approval: bool = False,
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
            requires_approval=requires_approval,
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
            requires_approval=False,
            invitee_id=user_id,
        )

        if self.notifications_service is not None:
            await self.notifications_service.create_notification(
                user_id=user_id,
                kind="space_invite",
                source_type="space",
                source_id=space_id,
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
    ) -> tuple[Literal["joined", "pending"], SpaceView | None, SpaceJoinRequestView | None]:
        invite = await self.repo.get_invite_by_code(code)
        if invite is None:
            raise AppError(
                code="INVITE_NOT_FOUND",
                message="Invite link not found",
                status_code=404,
            )

        if getattr(invite, "invitee_id", None) is not None and str(invite.invitee_id) != str(user_id):
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

        # Check existing membership
        existing = await self.repo.get_membership(space_id=space.str_id, user_id=user_id)
        if existing is not None:
            return "joined", self._to_view(space), None

        if invite.requires_approval:
            pending = await self.repo.get_pending_join_request(
                space_id=space.str_id, user_id=user_id
            )
            if pending is None:
                pending = await self.repo.create_join_request(
                    space_id=space.str_id,
                    user_id=user_id,
                    invite_code=code,
                )
            return "pending", None, self._to_join_request_view(pending)

        consumed = await self.repo.consume_invite_use(code=code)
        if consumed is None:
            raise AppError(
                code="INVITE_UNAVAILABLE",
                message="Invite link is revoked, expired, or fully used",
                status_code=410,
            )

        await self.repo.ensure_membership(
            space_id=space.str_id,
            user_id=user_id,
            role=ROLE_MEMBER,
        )

        return "joined", self._to_view(space), None

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

    async def request_join(self, *, user_id: str, space_id: str) -> SpaceJoinRequestView:
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
        pending = await self.repo.get_pending_join_request(space_id=space_id, user_id=user_id)
        if pending is None:
            pending = await self.repo.create_join_request(space_id=space_id, user_id=user_id)
        return self._to_join_request_view(pending)

    def _to_view(self, space: SpaceDocument, viewer_role: str | None = None) -> SpaceView:
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
        )

    async def list_channels(self, *, space_id: str, user_id: str) -> list[SpaceChannelView]:
        is_member = await self.check_membership(space_id=space_id, user_id=user_id)
        if not is_member:
            raise AppError(
                code="SPACE_FORBIDDEN",
                message="Not a member of this space",
                status_code=403,
            )

        from app.db.models import ConversationDocument
        from app.db.object_id import parse_object_id

        sp_id = parse_object_id(space_id)
        channels = await ConversationDocument.find({
            "space_id": sp_id,
            "type": "channel",
            "$or": [
                {"space_visibility": "space_public"},
                {"participant_ids": str(user_id)}
            ]
        }).to_list()

        return [
            SpaceChannelView(
                id=c.str_id,
                title=c.title,
                description=c.description,
                space_visibility=c.space_visibility,
                joined=str(user_id) in c.participant_ids,
            )
            for c in channels
        ]

    async def join_channel(self, *, space_id: str, conversation_id: str, user_id: str) -> None:
        is_member = await self.check_membership(space_id=space_id, user_id=user_id)
        if not is_member:
            raise AppError(
                code="SPACE_FORBIDDEN",
                message="Not a member of this space",
                status_code=403,
            )

        from app.db.models import ConversationDocument
        from app.db.object_id import parse_object_id

        conv = await ConversationDocument.get(parse_object_id(conversation_id))
        if conv is None or conv.type != "channel" or getattr(conv, "space_id", None) != parse_object_id(space_id):
            raise AppError(
                code="CONVERSATION_NOT_FOUND",
                message="Channel not found in this space",
                status_code=404,
            )

        if conv.space_visibility != "space_public":
            raise AppError(
                code="CHANNEL_JOIN_FORBIDDEN",
                message="Cannot join an invite-only channel",
                status_code=403,
            )

        from app.modules.conversations.repository import ConversationsRepository
        conv_repo = ConversationsRepository()

        await conv_repo.ensure_participant(
            conversation_id=conversation_id,
            user_id=user_id,
            role=ROLE_MEMBER,
        )
        await conv_repo.add_participant_id(
            conversation_id=conversation_id,
            user_id=user_id,
        )

    async def list_members(self, *, space_id: str, user_id: str) -> list[SpaceMemberView]:
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
                    username=users_map[str(m.user_id)].username if str(m.user_id) in users_map else None,
                    display_name=users_map[str(m.user_id)].display_name if str(m.user_id) in users_map else None,
                    avatar=users_map[str(m.user_id)].avatar if str(m.user_id) in users_map else None,
                )
            )
            for m in memberships
        ]

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

        membership = await self.repo.get_membership(space_id=space_id, user_id=actor_user_id)
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
            use_count=invite.use_count,
            requires_approval=invite.requires_approval,
            revoked=invite.revoked,
            invitee_id=getattr(invite, "invitee_id", None),
        )

    def _to_join_request_view(self, request: JoinRequestDocument) -> SpaceJoinRequestView:
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
