from __future__ import annotations

from datetime import datetime

from pymongo.errors import DuplicateKeyError

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

    async def create_channel_conversation(
        self,
        *,
        user_id: str,
        title: str,
        participant_ids: list[str],
        description: str | None = None,
        visibility: str = "private",
        posting_policy: str = "admins",
        slug: str | None = None,
    ) -> ConversationDocument:
        """Create a broadcast ``channel``.

        The creator becomes ``owner``; supplied participants join as ``member``.
        Public channels require a unique ``slug`` (enforced by the partial-unique
        index; a collision surfaces as a conflict error).
        """
        member_ids = sorted({str(pid) for pid in participant_ids if str(pid) != user_id})
        for participant_id in member_ids:
            await self._ensure_can_message(
                sender_id=user_id, receiver_id=participant_id
            )

        all_participants = sorted({str(user_id), *member_ids})
        try:
            conversation = await self.repo.create_channel(
                created_by=user_id,
                participant_ids=all_participants,
                title=title.strip(),
                description=description.strip() if description else None,
                visibility=visibility,
                posting_policy=posting_policy,
                slug=slug,
            )
        except DuplicateKeyError as exc:
            raise AppError(
                code="CONVERSATION_SLUG_TAKEN",
                message="A public conversation with this slug already exists",
                status_code=409,
            ) from exc

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

    async def ensure_thread_conversation(
        self, *, user_id: str, parent_conversation_id: str, root_message_id: str
    ) -> ConversationDocument:
        """Create-or-get the addressable ``thread`` sub-conversation for a message.

        The caller must be a participant of the parent conversation and the root
        message must belong to it. The thread carries its own participant list and
        per-participant read state, rooted in the parent + root message.
        """
        parent = await self.repo.get_for_participant(
            conversation_id=parent_conversation_id, user_id=user_id
        )
        if parent is None:
            raise AppError(
                code="CONVERSATION_NOT_FOUND",
                message="Conversation not found",
                status_code=404,
            )
        message = await self.repo.get_conversation_message(
            conversation_id=parent.str_id, message_id=root_message_id
        )
        if message is None:
            raise AppError(
                code="MESSAGE_NOT_FOUND",
                message="Root message not found in this conversation",
                status_code=404,
            )

        thread = await self.repo.ensure_thread(
            parent_conversation_id=parent.str_id,
            root_message_id=root_message_id,
            created_by=user_id,
        )
        await self.repo.ensure_participant(
            conversation_id=thread.str_id, user_id=user_id, role="owner"
        )
        await self.repo.add_participant_id(
            conversation_id=thread.str_id, user_id=user_id
        )
        sender_id = str(message.sender_id)
        if sender_id != str(user_id):
            await self.repo.ensure_participant(
                conversation_id=thread.str_id, user_id=sender_id, role="member"
            )
            await self.repo.add_participant_id(
                conversation_id=thread.str_id, user_id=sender_id
            )
        await self.repo.sync_member_count(conversation_id=thread.str_id)
        refreshed = await self.repo.get_by_id(thread.str_id)
        assert refreshed is not None
        return refreshed

    async def get_public_conversation_by_slug(
        self, *, slug: str
    ) -> ConversationDocument:
        """Look up a discoverable public conversation by its unique slug."""
        conversation = await self.repo.get_public_by_slug(slug)
        if conversation is None:
            raise AppError(
                code="CONVERSATION_NOT_FOUND",
                message="Public conversation not found",
                status_code=404,
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
