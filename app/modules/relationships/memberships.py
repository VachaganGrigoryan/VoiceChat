from __future__ import annotations

from typing import Any, Literal

from app.core.errors import AppError
from app.db.models import (
    ChannelDocument,
    ConversationDocument,
    RelationshipDocument,
    SpaceDocument,
)
from app.db.models.relationship import RelationshipTargetType
from app.db.object_id import parse_object_id
from app.modules.relationships.repository import RelationshipsRepository
from app.modules.relationships.service import RelationshipService

JoinPolicy = Literal["open", "approval", "invite_only", "closed"]

_MEMBERSHIP_TARGETS: set[RelationshipTargetType] = {"conversation", "channel", "space"}
# Inbox/read fields a caller may patch on a membership's `state` bag.
_STATE_FIELDS: frozenset[str] = frozenset(
    {
        "muted_until",
        "archived",
        "pinned",
        "hidden",
        "folder",
        "draft_text",
        "draft_updated_at",
        "last_read_at",
        "last_read_message_id",
        "notification_level",
    }
)


class MembershipService:
    """User↔resource participation (design §5).

    Requests and invites both land as `pending` and become `active` once the
    counterparty agrees; only `active` grants participation (§58). A pending
    membership *is* the join request — `JoinRequestDocument` has no successor.
    """

    def __init__(
        self,
        *,
        engine: RelationshipService | None = None,
        repo: RelationshipsRepository | None = None,
    ) -> None:
        self.repo = repo or RelationshipsRepository()
        self.engine = engine or RelationshipService(repo=self.repo)

    async def join_policy_for(
        self, *, target_type: RelationshipTargetType, target_id: str
    ) -> JoinPolicy:
        if target_type == "space":
            space = await SpaceDocument.get(parse_object_id(target_id))
            if space is None:
                raise AppError(
                    code="SPACE_NOT_FOUND", message="Space not found", status_code=404
                )
            return space.join_policy
        if target_type == "channel":
            channel = await ChannelDocument.get(parse_object_id(target_id))
            if channel is None:
                raise AppError(
                    code="CHANNEL_NOT_FOUND",
                    message="Channel not found",
                    status_code=404,
                )
            return channel.join_policy
        conversation = await ConversationDocument.get(parse_object_id(target_id))
        if conversation is None:
            raise AppError(
                code="CONVERSATION_NOT_FOUND",
                message="Conversation not found",
                status_code=404,
            )
        if conversation.type == "dm":
            raise AppError(
                code="CONVERSATION_JOIN_FORBIDDEN",
                message="A DM cannot be joined",
                status_code=403,
            )
        # Groups have no dedicated join_policy column; public groups are open,
        # private ones are invite-only.
        return "open" if conversation.visibility == "public" else "invite_only"

    async def _default_role_ids(
        self, *, target_type: RelationshipTargetType, target_id: str
    ) -> list[str] | None:
        """The roles a new member gets when the caller names none.

        Only spaces carry `default_role_ids`: a space role is what inherits down
        to child channels and groups, so a member holding no role at all would
        have an active membership that grants nothing (§53, §63). Conversation
        and channel memberships keep their existing behavior.
        """
        if target_type != "space":
            return None
        space = await SpaceDocument.get(parse_object_id(target_id))
        if space is None or not space.default_role_ids:
            return None
        return [str(role_id) for role_id in space.default_role_ids]

    async def request_join(
        self,
        *,
        user_id: str,
        target_type: RelationshipTargetType,
        target_id: str,
        role_ids: list[str] | None = None,
    ) -> RelationshipDocument:
        self._check_target(target_type)
        policy = await self.join_policy_for(target_type=target_type, target_id=target_id)
        if policy == "closed":
            raise AppError(
                code="JOIN_CLOSED",
                message="This resource is not accepting members",
                status_code=403,
            )
        if policy == "invite_only":
            raise AppError(
                code="JOIN_INVITE_ONLY",
                message="This resource is invite-only",
                status_code=403,
            )
        if role_ids is None:
            role_ids = await self._default_role_ids(
                target_type=target_type, target_id=target_id
            )
        return await self.engine.request(
            kind="membership",
            user_id=user_id,
            target_type=target_type,
            target_id=target_id,
            status="active" if policy == "open" else "pending",
            role_ids=role_ids,
        )

    async def invite(
        self,
        *,
        user_id: str,
        target_type: RelationshipTargetType,
        target_id: str,
        invited_by: str,
        role_ids: list[str] | None = None,
    ) -> RelationshipDocument:
        self._check_target(target_type)
        if role_ids is None:
            role_ids = await self._default_role_ids(
                target_type=target_type, target_id=target_id
            )
        return await self.engine.invite(
            kind="membership",
            user_id=user_id,
            target_type=target_type,
            target_id=target_id,
            invited_by=invited_by,
            role_ids=role_ids,
        )

    async def add(
        self,
        *,
        user_id: str,
        target_type: RelationshipTargetType,
        target_id: str,
        added_by: str | None = None,
        role_ids: list[str] | None = None,
    ) -> RelationshipDocument:
        """Idempotently make ``user_id`` an active member (authority-driven)."""
        self._check_target(target_type)
        if role_ids is None:
            role_ids = await self._default_role_ids(
                target_type=target_type, target_id=target_id
            )
        return await self.repo.upsert_membership(
            user_id=user_id,
            target_type=target_type,
            target_id=target_id,
            role_ids=role_ids,
            initiated_by=added_by or user_id,
            initiation="direct" if added_by is None else "invite",
        )

    async def accept(self, *, user_id: str, relationship_id: str) -> RelationshipDocument:
        """Accept an invite (the invitee) — approvals go through `approve`."""
        doc = await self._require(relationship_id)
        if str(doc.user_id) != str(user_id):
            raise AppError(
                code="MEMBERSHIP_FORBIDDEN",
                message="Not allowed to accept this membership",
                status_code=403,
            )
        # Only an invite is the caller's to accept. A join *request* has the
        # requester as `user_id` too, so without this check they could accept
        # their own request and self-activate, bypassing `member.approve`.
        if doc.initiation != "invite":
            raise AppError(
                code="MEMBERSHIP_NOT_INVITED",
                message="This membership is a join request; it needs approval",
                status_code=403,
            )
        return await self.engine.accept(relationship_id=doc.str_id, approved_by=user_id)

    async def approve(
        self, *, approver_user_id: str, relationship_id: str
    ) -> RelationshipDocument:
        """Approve a join request. Caller must have already checked authority."""
        doc = await self._require(relationship_id)
        return await self.engine.accept(
            relationship_id=doc.str_id, approved_by=approver_user_id
        )

    async def decline(
        self, *, user_id: str, relationship_id: str
    ) -> RelationshipDocument:
        doc = await self._require(relationship_id)
        return await self.engine.decline(
            relationship_id=doc.str_id, declined_by=user_id
        )

    async def revoke(
        self,
        *,
        user_id: str,
        target_type: RelationshipTargetType,
        target_id: str,
    ) -> RelationshipDocument | None:
        doc = await self.repo.find_edge(
            kind="membership",
            user_id=user_id,
            target_type=target_type,
            target_id=target_id,
        )
        if doc is None:
            return None
        return await self.engine.revoke(relationship_id=doc.str_id)

    async def get(
        self,
        *,
        user_id: str,
        target_type: RelationshipTargetType,
        target_id: str,
    ) -> RelationshipDocument | None:
        return await self.repo.find_edge(
            kind="membership",
            user_id=user_id,
            target_type=target_type,
            target_id=target_id,
        )

    async def is_active_member(
        self,
        *,
        user_id: str,
        target_type: RelationshipTargetType,
        target_id: str,
    ) -> bool:
        doc = await self.get(
            user_id=user_id, target_type=target_type, target_id=target_id
        )
        return doc is not None and doc.status == "active"

    async def list_members(
        self,
        *,
        target_type: RelationshipTargetType,
        target_id: str,
        status: str | None = "active",
        limit: int = 500,
    ) -> list[RelationshipDocument]:
        return await self.repo.list_for_target(
            kind="membership",
            target_type=target_type,
            target_id=target_id,
            status=status,  # type: ignore[arg-type]
            limit=limit,
        )

    async def list_for_user(
        self,
        *,
        user_id: str,
        target_type: RelationshipTargetType | None = None,
        status: str | None = "active",
        limit: int = 200,
    ) -> list[RelationshipDocument]:
        return await self.repo.list_for_user(
            kind="membership",
            user_id=user_id,
            target_type=target_type,
            status=status,  # type: ignore[arg-type]
            limit=limit,
        )

    async def update_state(
        self,
        *,
        user_id: str,
        target_type: RelationshipTargetType,
        target_id: str,
        updates: dict[str, Any],
    ) -> RelationshipDocument | None:
        """Patch the caller's inbox/read state on their membership."""
        allowed = {key: value for key, value in updates.items() if key in _STATE_FIELDS}
        if not allowed:
            return None
        return await self.repo.update_state_for_edge(
            kind="membership",
            user_id=user_id,
            target_type=target_type,
            target_id=target_id,
            updates=allowed,
        )

    def _check_target(self, target_type: RelationshipTargetType) -> None:
        if target_type not in _MEMBERSHIP_TARGETS:
            raise AppError(
                code="RELATIONSHIP_INVALID_TARGET",
                message="Membership targets a conversation, channel, or space",
                status_code=400,
            )

    async def _require(self, relationship_id: str) -> RelationshipDocument:
        doc = await self.repo.find_by_id(relationship_id)
        if doc is None or doc.kind != "membership":
            raise AppError(
                code="MEMBERSHIP_NOT_FOUND",
                message="Membership not found",
                status_code=404,
            )
        return doc
