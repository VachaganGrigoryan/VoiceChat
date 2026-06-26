from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.core.errors import AppError
from app.modules.messages.repository.mappers import to_message_doc
from app.modules.messages.service.base import EDIT_WINDOW_MINUTES


class UpdateMessagesMixin:
    async def mark_delivered(self, *, message_id: str, receiver_id: str):
        doc = await self.repo.mark_delivered_for_receiver(
            message_id=message_id,
            receiver_id=receiver_id,
        )
        return to_message_doc(doc)

    async def mark_read(self, *, message_id: str, receiver_id: str):
        doc = await self.repo.mark_read_for_receiver(
            message_id=message_id,
            receiver_id=receiver_id,
        )
        return to_message_doc(doc)

    async def mark_conversation_read(
        self, *, receiver_id: str, peer_user_id: str
    ) -> int:
        return await self.repo.mark_conversation_read_for_receiver(
            receiver_id=receiver_id,
            peer_user_id=peer_user_id,
        )

    async def edit_text_message(self, *, message_id: str, sender_id: str, text: str):
        existing = await self.repo.get_by_id(message_id=message_id)
        if not existing:
            raise AppError(
                code="MESSAGE_NOT_FOUND", message="Message not found", status_code=404
            )

        created_at = existing.created_at
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=UTC)

        if datetime.now(UTC) - created_at > timedelta(minutes=EDIT_WINDOW_MINUTES):
            raise AppError(
                code="EDIT_WINDOW_EXPIRED",
                message="Edit window expired",
                status_code=400,
            )

        doc = await self.repo.edit_text_message(
            message_id=message_id,
            sender_id=sender_id,
            text=self._normalize_text(text),
        )
        return to_message_doc(doc)
