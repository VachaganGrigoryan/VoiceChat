from __future__ import annotations

from app.modules.messages.repository.mappers import to_message_doc
from app.modules.messages.schemas import MessageDoc


class ReactionsMessagesMixin:
    async def add_reaction_for_conversation(
        self,
        *,
        conversation_id: str,
        message_id: str,
        user_id: str,
        emoji: str,
    ) -> MessageDoc:
        existing = await self.repo.get_by_id_for_conversation(
            conversation_id=conversation_id,
            message_id=message_id,
            user_id=user_id,
        )
        if existing is None:
            from app.core.errors import AppError

            raise AppError(
                code="MESSAGE_NOT_FOUND", message="Message not found", status_code=404
            )
        doc = await self.repo.add_or_toggle_grouped_reaction(
            message_id=message_id,
            user_id=user_id,
            emoji=self._normalize_emoji(emoji),
        )
        summaries = await self.repo.receipt_summaries_for_messages(
            conversation_id=doc.conversation_id,
            messages=[doc],
        )
        return to_message_doc(doc, receipt_summary=summaries.get(doc.str_id))

    async def remove_reaction_for_conversation(
        self,
        *,
        conversation_id: str,
        message_id: str,
        user_id: str,
        emoji: str,
    ) -> MessageDoc:
        existing = await self.repo.get_by_id_for_conversation(
            conversation_id=conversation_id,
            message_id=message_id,
            user_id=user_id,
        )
        if existing is None:
            from app.core.errors import AppError

            raise AppError(
                code="MESSAGE_NOT_FOUND", message="Message not found", status_code=404
            )
        doc = await self.repo.remove_grouped_reaction(
            message_id=message_id,
            user_id=user_id,
            emoji=self._normalize_emoji(emoji),
        )
        summaries = await self.repo.receipt_summaries_for_messages(
            conversation_id=doc.conversation_id,
            messages=[doc],
        )
        return to_message_doc(doc, receipt_summary=summaries.get(doc.str_id))
