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
from app.modules.spaces.repository import SpacesRepository
from app.modules.spaces.schemas import (
    SpaceView,
    SpaceInviteLinkView,
    SpaceJoinRequestView,
)

_MANAGER_ROLES = {"owner", "admin"}

class SpacesService:
    def __init__(self, repo: SpacesRepository) -> None:
        self.repo = repo

    async def create_space(
        self,
        *,
        created_by: str,
        name: str,
        slug: str,
        kind: Literal["workspace", "community"] = "workspace",
        visibility: Literal["private", "public"] = "private",
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
        space = SpaceDocument(
            name=name.strip(),
            slug=slug.strip().lower(),
            kind=kind,
            created_by=str(created_by),
            avatar=avatar,
            visibility=visibility,
            settings=settings or {},
            created_at=now,
            updated_at=now,
        )
        await space.insert()

        # Add creator as owner
        await self.repo.ensure_membership(
            space_id=space.str_id,
            user_id=created_by,
            role="owner",
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

        return self._to_view(space)

    async def get_space(self, *, space_id: str, user_id: str) -> SpaceView:
        space = await self.repo.get_by_id(space_id)
        if space is None:
            raise AppError(
                code="SPACE_NOT_FOUND",
                message="Space not found",
                status_code=404,
            )

        # Enforce membership for private spaces
        if space.visibility == "private":
            membership = await self.repo.get_membership(space_id=space_id, user_id=user_id)
            if membership is None:
                raise AppError(
                    code="SPACE_FORBIDDEN",
                    message="Not a member of this space",
                    status_code=403,
                )

        return self._to_view(space)

    async def list_for_user(self, *, user_id: str) -> list[SpaceView]:
        memberships = await self.repo.list_memberships_for_user(user_id=user_id)
        spaces: list[SpaceView] = []
        for member in memberships:
            space = await self.repo.get_by_id(str(member.space_id))
            if space is not None:
                spaces.append(self._to_view(space))
        return spaces

    async def check_membership(self, *, space_id: str, user_id: str) -> bool:
        membership = await self.repo.get_membership(space_id=space_id, user_id=user_id)
        return membership is not None

    async def require_manager(self, *, space_id: str, user_id: str) -> SpaceDocument:
        space = await self.repo.get_by_id(space_id)
        if space is None:
            raise AppError(
                code="SPACE_NOT_FOUND",
                message="Space not found",
                status_code=404,
            )
        membership = await self.repo.get_membership(space_id=space_id, user_id=user_id)
        if membership is None or membership.role not in _MANAGER_ROLES:
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
        await self.require_manager(space_id=space_id, user_id=actor_user_id)
        invite = await self.repo.create_invite_link(
            target_id=space_id,
            created_by=actor_user_id,
            code=secrets.token_urlsafe(12),
            expires_at=expires_at,
            max_uses=max_uses,
            requires_approval=requires_approval,
        )
        return self._to_invite_view(invite)

    async def list_invites(
        self, *, actor_user_id: str, space_id: str
    ) -> list[SpaceInviteLinkView]:
        await self.require_manager(space_id=space_id, user_id=actor_user_id)
        invites = await self.repo.list_invites_for_space(space_id=space_id)
        return [self._to_invite_view(inv) for inv in invites]

    async def revoke_invite(
        self, *, actor_user_id: str, space_id: str, invite_id: str
    ) -> None:
        await self.require_manager(space_id=space_id, user_id=actor_user_id)
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
            role="member",
        )

        return "joined", self._to_view(space), None

    async def list_join_requests(
        self, *, actor_user_id: str, space_id: str
    ) -> list[SpaceJoinRequestView]:
        await self.require_manager(space_id=space_id, user_id=actor_user_id)
        requests = await self.repo.list_pending_join_requests(space_id=space_id)
        return [self._to_join_request_view(req) for req in requests]

    async def approve_join_request(
        self, *, actor_user_id: str, space_id: str, request_id: str
    ) -> SpaceJoinRequestView:
        await self.require_manager(space_id=space_id, user_id=actor_user_id)
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
            role="member",
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
        await self.require_manager(space_id=space_id, user_id=actor_user_id)
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

    def _to_view(self, space: SpaceDocument) -> SpaceView:
        return SpaceView(
            id=space.str_id,
            name=space.name,
            slug=space.slug,
            kind=space.kind,
            created_by=str(space.created_by),
            avatar=space.avatar,
            visibility=space.visibility,
            settings=space.settings,
            created_at=space.created_at,
            updated_at=space.updated_at,
        )

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
