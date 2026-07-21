from __future__ import annotations

from app.core.errors import AppError
from app.db.models import ConversationDocument, ParticipantDocument
from app.modules.conversations.service.base import BaseConversationsService


class ParticipantsServiceMixin(BaseConversationsService):
    async def _get_group_for_participant(
        self, *, conversation_id: str, user_id: str
    ) -> ConversationDocument:
        conversation = await self.repo.get_for_participant(
            conversation_id=conversation_id, user_id=user_id
        )
        if conversation is None:
            raise AppError(
                code="CONVERSATION_NOT_FOUND",
                message="Conversation not found",
                status_code=404,
            )
        if conversation.type != "group":
            raise AppError(
                code="CONVERSATION_NOT_GROUP",
                message="Conversation is not a group",
                status_code=400,
            )
        return conversation

    async def _get_participant_or_404(
        self, *, conversation_id: str, user_id: str
    ) -> ParticipantDocument:
        participant = await self.repo.get_participant(
            conversation_id=conversation_id, user_id=user_id
        )
        if participant is None:
            raise AppError(
                code="PARTICIPANT_NOT_FOUND",
                message="Participant not found",
                status_code=404,
            )
        return participant

    async def _require_actor_role(
        self,
        *,
        conversation_id: str,
        user_id: str,
        allowed_roles: set[str],
    ) -> ParticipantDocument:
        participant = await self._get_participant_or_404(
            conversation_id=conversation_id, user_id=user_id
        )
        if participant.role not in allowed_roles:
            raise AppError(
                code="CONVERSATION_FORBIDDEN",
                message="Not allowed to manage this conversation",
                status_code=403,
            )
        return participant

    async def mark_conversation_read(
        self,
        *,
        user_id: str,
        conversation_id: str,
        last_read_message_id: str | None = None,
    ) -> None:
        conversation = await self.repo.get_for_participant(
            conversation_id=conversation_id, user_id=user_id
        )
        if conversation is None:
            raise AppError(
                code="CONVERSATION_NOT_FOUND",
                message="Conversation not found",
                status_code=404,
            )
        await self.repo.mark_read(
            conversation_id=conversation.str_id,
            user_id=user_id,
            last_read_message_id=last_read_message_id,
        )

    async def list_group_participants(
        self, *, user_id: str, conversation_id: str
    ) -> list[ParticipantDocument]:
        conversation = await self._get_group_for_participant(
            conversation_id=conversation_id, user_id=user_id
        )
        return await self.repo.list_participants(conversation_id=conversation.str_id)

    async def add_group_members(
        self, *, actor_user_id: str, conversation_id: str, participant_ids: list[str]
    ) -> list[ParticipantDocument]:
        conversation = await self._get_group_for_participant(
            conversation_id=conversation_id, user_id=actor_user_id
        )
        await self._require_actor_role(
            conversation_id=conversation.str_id,
            user_id=actor_user_id,
            allowed_roles={"owner", "admin"},
        )

        added: list[ParticipantDocument] = []
        for participant_id in sorted(
            {str(pid) for pid in participant_ids if str(pid) != actor_user_id}
        ):
            await self._ensure_can_message(
                sender_id=actor_user_id, receiver_id=participant_id
            )
            participant = await self.repo.ensure_participant(
                conversation_id=conversation.str_id,
                user_id=participant_id,
                role="member",
            )
            await self.repo.add_participant_id(
                conversation_id=conversation.str_id,
                user_id=participant_id,
            )
            added.append(participant)
        return added

    async def remove_group_member(
        self, *, actor_user_id: str, conversation_id: str, target_user_id: str
    ) -> None:
        conversation = await self._get_group_for_participant(
            conversation_id=conversation_id, user_id=actor_user_id
        )
        actor = await self._require_actor_role(
            conversation_id=conversation.str_id,
            user_id=actor_user_id,
            allowed_roles={"owner", "admin"},
        )
        target = await self._get_participant_or_404(
            conversation_id=conversation.str_id, user_id=target_user_id
        )

        if target.role == "owner" or (actor.role == "admin" and target.role != "member"):
            raise AppError(
                code="CONVERSATION_FORBIDDEN",
                message="Not allowed to remove this participant",
                status_code=403,
            )

        await self.repo.delete_participant(
            conversation_id=conversation.str_id, user_id=target_user_id
        )
        await self.repo.remove_participant_id(
            conversation_id=conversation.str_id, user_id=target_user_id
        )

    async def update_group_member_role(
        self,
        *,
        actor_user_id: str,
        conversation_id: str,
        target_user_id: str,
        role: str,
    ) -> ParticipantDocument:
        conversation = await self._get_group_for_participant(
            conversation_id=conversation_id, user_id=actor_user_id
        )
        await self._require_actor_role(
            conversation_id=conversation.str_id,
            user_id=actor_user_id,
            allowed_roles={"owner"},
        )
        target = await self._get_participant_or_404(
            conversation_id=conversation.str_id, user_id=target_user_id
        )
        if target.role == "owner":
            raise AppError(
                code="CONVERSATION_FORBIDDEN",
                message="Owner role can only change through ownership transfer",
                status_code=403,
            )
        updated = await self.repo.set_participant_role(
            conversation_id=conversation.str_id,
            user_id=target_user_id,
            role=role,
        )
        assert updated is not None
        return updated

    async def transfer_group_ownership(
        self, *, actor_user_id: str, conversation_id: str, target_user_id: str
    ) -> list[ParticipantDocument]:
        conversation = await self._get_group_for_participant(
            conversation_id=conversation_id, user_id=actor_user_id
        )
        await self._require_actor_role(
            conversation_id=conversation.str_id,
            user_id=actor_user_id,
            allowed_roles={"owner"},
        )
        await self._get_participant_or_404(
            conversation_id=conversation.str_id, user_id=target_user_id
        )
        if actor_user_id == target_user_id:
            raise AppError(
                code="INVALID_CONVERSATION",
                message="Cannot transfer ownership to yourself",
                status_code=400,
            )

        new_owner = await self.repo.set_participant_role(
            conversation_id=conversation.str_id,
            user_id=target_user_id,
            role="owner",
        )
        previous_owner = await self.repo.set_participant_role(
            conversation_id=conversation.str_id,
            user_id=actor_user_id,
            role="admin",
        )
        assert new_owner is not None and previous_owner is not None
        return [new_owner, previous_owner]

    async def leave_group(self, *, user_id: str, conversation_id: str) -> None:
        conversation = await self._get_group_for_participant(
            conversation_id=conversation_id, user_id=user_id
        )
        participant = await self._get_participant_or_404(
            conversation_id=conversation.str_id, user_id=user_id
        )
        if participant.role == "owner":
            raise AppError(
                code="CONVERSATION_FORBIDDEN",
                message="Transfer ownership before leaving the group",
                status_code=403,
            )
        await self.repo.delete_participant(
            conversation_id=conversation.str_id, user_id=user_id
        )
        await self.repo.remove_participant_id(
            conversation_id=conversation.str_id, user_id=user_id
        )

    async def delete_group(self, *, user_id: str, conversation_id: str) -> None:
        conversation = await self._get_group_for_participant(
            conversation_id=conversation_id, user_id=user_id
        )
        await self._require_actor_role(
            conversation_id=conversation.str_id,
            user_id=user_id,
            allowed_roles={"owner"},
        )
        participants = await self.repo.list_participants(
            conversation_id=conversation.str_id
        )
        for participant in participants:
            await self.repo.delete_participant(
                conversation_id=conversation.str_id,
                user_id=str(participant.user_id),
            )
        await self.repo.delete_conversation(conversation_id=conversation.str_id)
