from __future__ import annotations

from app.core.errors import AppError
from app.db.models import MessageContainerType
from app.modules.authorization.permissions import (
    REACTION_CREATE,
    REACTION_DELETE_OWN,
)
from app.modules.messages.repository.mappers import to_message_doc
from app.modules.messages.schemas import MessageDoc


class ReactionsMessagesMixin:
    async def add_reaction(
        self,
        *,
        container_type: MessageContainerType,
        container_id: str,
        message_id: str,
        user_id: str,
        emoji: str,
    ) -> MessageDoc:
        await self._require_reactable(
            container_type=container_type,
            container_id=container_id,
            message_id=message_id,
            user_id=user_id,
            action=REACTION_CREATE,
        )
        doc = await self.repo.add_or_toggle_grouped_reaction(
            message_id=message_id,
            user_id=user_id,
            emoji=self._normalize_emoji(emoji),
        )
        return await self._reacted_message_doc(doc)

    async def remove_reaction(
        self,
        *,
        container_type: MessageContainerType,
        container_id: str,
        message_id: str,
        user_id: str,
        emoji: str,
    ) -> MessageDoc:
        await self._require_reactable(
            container_type=container_type,
            container_id=container_id,
            message_id=message_id,
            user_id=user_id,
            # Removing a reaction is always removing one's own (§59).
            action=REACTION_DELETE_OWN,
            own=True,
        )
        doc = await self.repo.remove_grouped_reaction(
            message_id=message_id,
            user_id=user_id,
            emoji=self._normalize_emoji(emoji),
        )
        return await self._reacted_message_doc(doc)

    async def _require_reactable(
        self,
        *,
        container_type: MessageContainerType,
        container_id: str,
        message_id: str,
        user_id: str,
        action: str,
        own: bool = False,
    ) -> None:
        existing = await self.repo.get_by_id_in_container(
            container_type=container_type,
            container_id=container_id,
            message_id=message_id,
            user_id=user_id,
        )
        if existing is None:
            raise AppError(
                code="MESSAGE_NOT_FOUND", message="Message not found", status_code=404
            )
        await self._require_container_permission(
            user_id=user_id,
            action=action,
            container_type=container_type,
            container_id=container_id,
            sender_id=user_id if own else None,
            message="Not allowed to react in this container",
        )

    async def _reacted_message_doc(self, doc) -> MessageDoc:
        summaries = await self.repo.receipt_summaries_for_messages(
            container_type=doc.container_type,
            container_id=doc.container_id,
            messages=[doc],
        )
        return to_message_doc(doc, receipt_summary=summaries.get(doc.str_id))
