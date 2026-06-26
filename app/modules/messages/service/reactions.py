from __future__ import annotations

from app.modules.messages.repository.mappers import to_message_doc
from app.modules.messages.schemas import MessageDoc


class ReactionsMessagesMixin:
    async def add_reaction(
        self,
        *,
        message_id: str,
        user_id: str,
        emoji: str,
    ) -> MessageDoc:
        doc = await self.repo.add_or_toggle_grouped_reaction(
            message_id=message_id,
            user_id=user_id,
            emoji=self._normalize_emoji(emoji),
        )
        return to_message_doc(doc)

    async def remove_reaction(
        self,
        *,
        message_id: str,
        user_id: str,
        emoji: str,
    ) -> MessageDoc:
        doc = await self.repo.remove_grouped_reaction(
            message_id=message_id,
            user_id=user_id,
            emoji=self._normalize_emoji(emoji),
        )
        return to_message_doc(doc)
