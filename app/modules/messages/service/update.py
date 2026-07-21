from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.core.errors import AppError
from app.modules.messages.repository.mappers import to_message_doc
from app.modules.messages.service.base import EDIT_WINDOW_MINUTES


class UpdateMessagesMixin:
    async def mark_delivered_for_conversation(
        self, *, conversation_id: str, message_id: str, user_id: str
    ):
        existing = await self.repo.get_by_id_for_conversation(
            conversation_id=conversation_id,
            message_id=message_id,
            user_id=user_id,
        )
        if not existing:
            raise AppError(
                code="MESSAGE_NOT_FOUND", message="Message not found", status_code=404
            )
        summary = await self.repo.upsert_message_receipt(
            conversation_id=conversation_id,
            message_id=message_id,
            user_id=user_id,
            delivered=True,
        )
        return to_message_doc(existing, receipt_summary=summary)

    async def mark_read_for_conversation(
        self, *, conversation_id: str, message_id: str, user_id: str
    ):
        existing = await self.repo.get_by_id_for_conversation(
            conversation_id=conversation_id,
            message_id=message_id,
            user_id=user_id,
        )
        if not existing:
            raise AppError(
                code="MESSAGE_NOT_FOUND", message="Message not found", status_code=404
            )
        summary = await self.repo.upsert_message_receipt(
            conversation_id=conversation_id,
            message_id=message_id,
            user_id=user_id,
            delivered=True,
            read=True,
        )
        return to_message_doc(existing, receipt_summary=summary)

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
        summaries = await self.repo.receipt_summaries_for_messages(
            conversation_id=doc.conversation_id,
            messages=[doc],
        )
        return to_message_doc(doc, receipt_summary=summaries.get(doc.str_id))

    async def edit_text_message_for_conversation(
        self,
        *,
        conversation_id: str,
        message_id: str,
        sender_id: str,
        text: str,
    ):
        existing = await self.repo.get_by_id_for_conversation(
            conversation_id=conversation_id,
            message_id=message_id,
            user_id=sender_id,
        )
        if not existing:
            raise AppError(
                code="MESSAGE_NOT_FOUND", message="Message not found", status_code=404
            )
        return await self.edit_text_message(
            message_id=message_id, sender_id=sender_id, text=text
        )
