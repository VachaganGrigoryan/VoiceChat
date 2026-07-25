from __future__ import annotations

from datetime import UTC, datetime

from app.core.errors import AppError
from app.db.models import ConversationDocument, ParticipantDocument
from app.modules.authorization.permissions import MESSAGE_PIN
from app.modules.conversations.service.base import BaseConversationsService


class PowerFeaturesServiceMixin(BaseConversationsService):
    """Pinned messages and per-participant drafts (message power features)."""

    async def _require_conversation_message(
        self, *, conversation_id: str, message_id: str
    ):
        message = await self.repo.get_conversation_message(
            conversation_id=conversation_id, message_id=message_id
        )
        if message is None:
            raise AppError(
                code="MESSAGE_NOT_FOUND",
                message="Message not found",
                status_code=404,
            )
        return message

    async def _require_pin_permission(
        self, *, user_id: str, conversation_id: str
    ) -> ConversationDocument:
        conversation = await self.require_participant(
            user_id=user_id, conversation_id=conversation_id
        )
        if conversation.type == "dm":
            return conversation

        await self.require_permission(
            user_id=user_id, conversation_id=conversation_id, permission=MESSAGE_PIN
        )
        return conversation

    async def pin_message(
        self, *, user_id: str, conversation_id: str, message_id: str
    ) -> ConversationDocument:
        await self._require_pin_permission(
            user_id=user_id, conversation_id=conversation_id
        )
        await self._require_conversation_message(
            conversation_id=conversation_id, message_id=message_id
        )
        updated = await self.repo.add_pinned_message(
            conversation_id=conversation_id, message_id=message_id
        )
        if updated is None:
            raise AppError(
                code="CONVERSATION_NOT_FOUND",
                message="Conversation not found",
                status_code=404,
            )
        return updated

    async def unpin_message(
        self, *, user_id: str, conversation_id: str, message_id: str
    ) -> ConversationDocument:
        await self._require_pin_permission(
            user_id=user_id, conversation_id=conversation_id
        )
        updated = await self.repo.remove_pinned_message(
            conversation_id=conversation_id, message_id=message_id
        )
        if updated is None:
            raise AppError(
                code="CONVERSATION_NOT_FOUND",
                message="Conversation not found",
                status_code=404,
            )
        return updated

    async def list_pinned_message_ids(
        self, *, user_id: str, conversation_id: str
    ) -> list[str]:
        conversation = await self.require_participant(
            user_id=user_id, conversation_id=conversation_id
        )
        return [str(message_id) for message_id in conversation.pinned_message_ids]

    async def set_draft(
        self, *, user_id: str, conversation_id: str, text: str | None
    ) -> ParticipantDocument:
        conversation = await self.require_participant(
            user_id=user_id, conversation_id=conversation_id
        )
        normalized = (text or "").strip() or None
        participant = await self.repo.set_participant_draft(
            conversation_id=conversation.str_id,
            user_id=user_id,
            draft_text=normalized,
            draft_updated_at=datetime.now(UTC) if normalized is not None else None,
        )
        if participant is None:
            raise AppError(
                code="PARTICIPANT_NOT_FOUND",
                message="Participant not found",
                status_code=404,
            )
        return participant

    async def get_draft(
        self, *, user_id: str, conversation_id: str
    ) -> ParticipantDocument:
        conversation = await self.require_participant(
            user_id=user_id, conversation_id=conversation_id
        )
        participant = await self.repo.get_participant(
            conversation_id=conversation.str_id, user_id=user_id
        )
        if participant is None:
            raise AppError(
                code="PARTICIPANT_NOT_FOUND",
                message="Participant not found",
                status_code=404,
            )
        return participant

    async def clear_draft(self, *, user_id: str, conversation_id: str) -> None:
        """Clear the caller's draft. Best-effort: never fails the send path."""
        conversation = await self.repo.get_for_participant(
            conversation_id=conversation_id, user_id=user_id
        )
        if conversation is None:
            return
        await self.repo.set_participant_draft(
            conversation_id=conversation.str_id,
            user_id=user_id,
            draft_text=None,
            draft_updated_at=None,
        )
