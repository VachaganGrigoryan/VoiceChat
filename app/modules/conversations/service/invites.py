from __future__ import annotations

import secrets

from app.core.errors import AppError
from app.db.models import (
    ConversationDocument,
    InviteLinkDocument,
    JoinRequestDocument,
    ParticipantDocument,
)
from app.modules.authorization.permissions import MEMBER_APPROVE, MEMBER_INVITE
from app.modules.authorization.roles import ROLE_MEMBER
from app.modules.conversations.service.base import BaseConversationsService


class InvitesServiceMixin(BaseConversationsService):
    """Invite links and join requests for group/channel conversations."""

    async def _require_invite_manager(
        self, *, actor_user_id: str, conversation_id: str, permission: str = MEMBER_INVITE
    ) -> ConversationDocument:
        conversation = await self.repo.get_for_participant(
            conversation_id=conversation_id, user_id=actor_user_id
        )
        if conversation is None:
            raise AppError(
                code="CONVERSATION_NOT_FOUND",
                message="Conversation not found",
                status_code=404,
            )
        if conversation.type not in {"group", "channel"}:
            raise AppError(
                code="CONVERSATION_NOT_INVITABLE",
                message="Only group or channel conversations support invites",
                status_code=400,
            )
        await self.authorization.require(
            actor_user_id,
            permission,
            "conversation",
            conversation.str_id,
            message="Not allowed to manage invites for this conversation",
        )
        return conversation

    async def create_invite(
        self,
        *,
        actor_user_id: str,
        conversation_id: str,
        expires_at=None,
        max_uses: int | None = None,
        requires_approval: bool = False,
    ) -> InviteLinkDocument:
        conversation = await self._require_invite_manager(
            actor_user_id=actor_user_id, conversation_id=conversation_id
        )
        return await self.repo.create_invite_link(
            target_id=conversation.str_id,
            created_by=actor_user_id,
            code=secrets.token_urlsafe(12),
            expires_at=expires_at,
            max_uses=max_uses,
            requires_approval=requires_approval,
        )

    async def list_invites(
        self, *, actor_user_id: str, conversation_id: str
    ) -> list[InviteLinkDocument]:
        conversation = await self._require_invite_manager(
            actor_user_id=actor_user_id, conversation_id=conversation_id
        )
        return await self.repo.list_invites_for_conversation(
            conversation_id=conversation.str_id
        )

    async def revoke_invite(
        self, *, actor_user_id: str, conversation_id: str, invite_id: str
    ) -> None:
        conversation = await self._require_invite_manager(
            actor_user_id=actor_user_id, conversation_id=conversation_id
        )
        target = await self.repo.get_invite_by_id(invite_id=invite_id)
        if target is None or str(target.target_id) != conversation.str_id:
            raise AppError(
                code="INVITE_NOT_FOUND",
                message="Invite link not found",
                status_code=404,
            )
        await self.repo.revoke_invite(invite_id=invite_id)

    async def redeem_invite(
        self, *, user_id: str, code: str
    ) -> tuple[str, ConversationDocument | None, JoinRequestDocument | None]:
        """Redeem an invite code.

        Returns ``(status, conversation, join_request)`` where status is
        ``"joined"`` (participant added) or ``"pending"`` (approval required).
        """
        invite = await self.repo.get_invite_by_code(code)
        if invite is None or invite.target_type != "conversation":
            raise AppError(
                code="INVITE_NOT_FOUND", message="Invite link not found", status_code=404
            )
        conversation = await self.repo.get_by_id(str(invite.target_id))
        if conversation is None:
            raise AppError(
                code="CONVERSATION_NOT_FOUND",
                message="Conversation not found",
                status_code=404,
            )

        # Already a member: idempotent success without consuming a use.
        existing = await self.repo.get_participant(
            conversation_id=conversation.str_id, user_id=user_id
        )
        if existing is not None and str(user_id) in {
            str(pid) for pid in conversation.participant_ids
        }:
            return "joined", conversation, None

        if invite.requires_approval:
            pending = await self.repo.get_pending_join_request(
                conversation_id=conversation.str_id, user_id=user_id
            )
            if pending is None:
                pending = await self.repo.create_join_request(
                    conversation_id=conversation.str_id,
                    user_id=user_id,
                    invite_code=code,
                )
            return "pending", None, pending

        consumed = await self.repo.consume_invite_use(code=code)
        if consumed is None:
            raise AppError(
                code="INVITE_UNAVAILABLE",
                message="Invite link is revoked, expired, or fully used",
                status_code=410,
            )
        await self._add_member(
            conversation_id=conversation.str_id,
            user_id=user_id,
            invited_by=str(invite.created_by),
        )
        refreshed = await self.repo.get_by_id(conversation.str_id)
        return "joined", refreshed, None

    async def list_join_requests(
        self, *, actor_user_id: str, conversation_id: str
    ) -> list[JoinRequestDocument]:
        conversation = await self._require_invite_manager(
            actor_user_id=actor_user_id,
            conversation_id=conversation_id,
            permission=MEMBER_APPROVE,
        )
        return await self.repo.list_pending_join_requests(
            conversation_id=conversation.str_id
        )

    async def approve_join_request(
        self, *, actor_user_id: str, conversation_id: str, request_id: str
    ) -> JoinRequestDocument:
        conversation = await self._require_invite_manager(
            actor_user_id=actor_user_id,
            conversation_id=conversation_id,
            permission=MEMBER_APPROVE,
        )
        request = await self._load_pending_request(
            conversation_id=conversation.str_id, request_id=request_id
        )
        updated = await self.repo.set_join_request_status(
            request_id=request_id, status="approved"
        )
        if updated is None:  # pragma: no cover - lost race, already resolved
            raise AppError(
                code="JOIN_REQUEST_RESOLVED",
                message="Join request has already been resolved",
                status_code=409,
            )
        await self._add_member(
            conversation_id=conversation.str_id,
            user_id=str(request.user_id),
            invited_by=actor_user_id,
        )
        return updated

    async def reject_join_request(
        self, *, actor_user_id: str, conversation_id: str, request_id: str
    ) -> JoinRequestDocument:
        conversation = await self._require_invite_manager(
            actor_user_id=actor_user_id,
            conversation_id=conversation_id,
            permission=MEMBER_APPROVE,
        )
        await self._load_pending_request(
            conversation_id=conversation.str_id, request_id=request_id
        )
        updated = await self.repo.set_join_request_status(
            request_id=request_id, status="rejected"
        )
        if updated is None:  # pragma: no cover - lost race, already resolved
            raise AppError(
                code="JOIN_REQUEST_RESOLVED",
                message="Join request has already been resolved",
                status_code=409,
            )
        return updated

    async def _load_pending_request(
        self, *, conversation_id: str, request_id: str
    ) -> JoinRequestDocument:
        request = await self.repo.get_join_request(request_id=request_id)
        if (
            request is None
            or str(request.target_id) != conversation_id
            or request.target_type != "conversation"
        ):
            raise AppError(
                code="JOIN_REQUEST_NOT_FOUND",
                message="Join request not found",
                status_code=404,
            )
        return request

    async def _add_member(
        self, *, conversation_id: str, user_id: str, invited_by: str | None
    ) -> ParticipantDocument:
        participant = await self.repo.ensure_participant(
            conversation_id=conversation_id, user_id=user_id, role=ROLE_MEMBER
        )
        await self.repo.add_participant_id(
            conversation_id=conversation_id, user_id=user_id
        )
        await self.repo.sync_member_count(conversation_id=conversation_id)
        return participant
