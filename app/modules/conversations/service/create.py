from __future__ import annotations

from datetime import datetime

from app.core.errors import AppError
from app.db.models import ConversationDocument, ConversationPreviewDocument
from app.modules.conversations.service.base import BaseConversationsService


class CreateConversationsMixin(BaseConversationsService):
    async def ensure_dm_conversation(
        self, *, user_id: str, peer_user_id: str
    ) -> ConversationDocument:
        """Create-or-get the DM between two users, enforcing DM-only + ping gate.

        Also ensures both `Participant` records exist so read state can be tracked.
        """
        self._require_distinct(user_id=user_id, peer_user_id=peer_user_id)
        await self._ensure_can_message(sender_id=user_id, receiver_id=peer_user_id)
        return await self._ensure_dm_entities(
            user_id=user_id, peer_user_id=peer_user_id
        )

    async def _ensure_dm_entities(
        self, *, user_id: str, peer_user_id: str
    ) -> ConversationDocument:
        conversation = await self.repo.ensure_dm(
            user_a=user_id, user_b=peer_user_id, created_by=user_id
        )
        conversation_id = conversation.str_id
        await self.repo.ensure_participant(
            conversation_id=conversation_id, user_id=user_id, role="owner"
        )
        await self.repo.ensure_participant(
            conversation_id=conversation_id, user_id=peer_user_id, role="member"
        )
        return conversation

    async def create_group_conversation(
        self, *, user_id: str, title: str, participant_ids: list[str]
    ) -> ConversationDocument:
        member_ids = sorted({str(pid) for pid in participant_ids if str(pid) != user_id})
        if not member_ids:
            raise AppError(
                code="INVALID_CONVERSATION",
                message="Group conversation requires at least one other participant",
                status_code=400,
            )

        for participant_id in member_ids:
            await self._ensure_can_message(
                sender_id=user_id, receiver_id=participant_id
            )

        all_participants = sorted({str(user_id), *member_ids})
        conversation = await self.repo.create_group(
            created_by=user_id,
            participant_ids=all_participants,
            title=title.strip(),
        )
        await self.repo.ensure_participant(
            conversation_id=conversation.str_id, user_id=user_id, role="owner"
        )
        for participant_id in member_ids:
            await self.repo.ensure_participant(
                conversation_id=conversation.str_id,
                user_id=participant_id,
                role="member",
            )
        return conversation

    async def materialize_dm_message(
        self,
        *,
        sender_id: str,
        receiver_id: str,
        message_id: str,
        message_type: str,
        preview_text: str | None,
        created_at: datetime,
    ) -> None:
        """Record a just-sent DM message against the conversation entity.

        Ensures the conversation + participants exist, advances the inbox preview,
        and marks the sender caught up on their own message. The ping gate is NOT
        re-checked here — the caller (message service) already enforced it.
        """
        conversation = await self._ensure_dm_entities(
            user_id=sender_id, peer_user_id=receiver_id
        )
        conversation_id = conversation.str_id

        preview = ConversationPreviewDocument(
            message_id=message_id,
            sender_id=str(sender_id),
            type=message_type,
            text=preview_text,
            created_at=created_at,
        )
        await self.repo.touch_last_message(
            conversation_id=conversation_id, preview=preview
        )
        await self.repo.mark_read(
            conversation_id=conversation_id,
            user_id=sender_id,
            last_read_message_id=message_id,
            last_read_at=created_at,
        )

    async def materialize_conversation_message(
        self,
        *,
        conversation_id: str,
        sender_id: str,
        message_id: str,
        message_type: str,
        preview_text: str | None,
        created_at: datetime,
    ) -> None:
        preview = ConversationPreviewDocument(
            message_id=message_id,
            sender_id=str(sender_id),
            type=message_type,
            text=preview_text,
            created_at=created_at,
        )
        await self.repo.touch_last_message(
            conversation_id=conversation_id, preview=preview
        )
        await self.repo.mark_read(
            conversation_id=conversation_id,
            user_id=sender_id,
            last_read_message_id=message_id,
            last_read_at=created_at,
        )
