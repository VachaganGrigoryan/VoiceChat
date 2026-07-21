from __future__ import annotations

import asyncio
from typing import Any

from app.core.errors import AppError
from app.db.models import ConversationDocument
from app.modules.conversations.repository.mappers import to_conversation_view
from app.modules.conversations.schemas import ConversationUserSummary, ConversationView
from app.modules.users.avatar import build_user_avatar_payload
from app.modules.conversations.service.base import BaseConversationsService


class ReadConversationsMixin(BaseConversationsService):
    async def list_for_user(
        self, *, user_id: str, limit: int, cursor: str | None
    ) -> tuple[list[ConversationDocument], str | None]:
        return await self.repo.list_for_user(
            user_id=user_id, limit=limit, cursor=cursor
        )

    async def require_participant(
        self, *, user_id: str, conversation_id: str
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
        return conversation

    async def unread_count_for(
        self, *, user_id: str, conversation: ConversationDocument
    ) -> int:
        participant = await self.repo.get_participant(
            conversation_id=conversation.str_id, user_id=user_id
        )
        return await self.repo.unread_count(
            message_conversation_id=conversation.str_id,
            user_id=user_id,
            last_read_at=participant.last_read_at if participant else None,
        )

    async def views_for_user(
        self, *, user_id: str, conversations: list[ConversationDocument]
    ) -> list[ConversationView]:
        participant_ids = list(
            dict.fromkeys(
                str(participant_id)
                for conversation in conversations
                for participant_id in conversation.participant_ids
            )
        )
        users_by_id = (
            await self.users_repo.find_by_ids(participant_ids)
            if self.users_repo is not None
            else {}
        )
        online_by_id = await self._presence_by_id(participant_ids)
        contact_state_by_peer = await self._contact_state_by_peer(
            user_id=user_id,
            peer_ids=[
                peer_id
                for conversation in conversations
                if conversation.type == "dm"
                for peer_id in [self._peer_id(conversation, user_id=user_id)]
                if peer_id is not None
            ],
        )

        views: list[ConversationView] = []
        for conversation in conversations:
            participant_users = [
                self._conversation_user_summary(
                    user_id=str(participant_id),
                    user=users_by_id.get(str(participant_id)),
                    is_online=online_by_id.get(str(participant_id), False),
                    contact_state=contact_state_by_peer.get(str(participant_id)),
                )
                for participant_id in conversation.participant_ids
            ]
            peer_id = (
                self._peer_id(conversation, user_id=user_id)
                if conversation.type == "dm"
                else None
            )
            peer_user = (
                self._conversation_user_summary(
                    user_id=peer_id,
                    user=users_by_id.get(peer_id),
                    is_online=online_by_id.get(peer_id, False),
                    contact_state=contact_state_by_peer.get(peer_id),
                )
                if peer_id is not None
                else None
            )
            views.append(
                to_conversation_view(
                    conversation,
                    unread_count=await self.unread_count_for(
                        user_id=user_id, conversation=conversation
                    ),
                    peer_user=peer_user,
                    participant_users=participant_users,
                )
            )
        return views

    async def _presence_by_id(self, user_ids: list[str]) -> dict[str, bool]:
        if not user_ids or self.presence_service is None:
            return {}
        statuses = await asyncio.gather(
            *(self.presence_service.is_online(user_id) for user_id in user_ids)
        )
        return dict(zip(user_ids, statuses))

    async def _contact_state_by_peer(
        self, *, user_id: str, peer_ids: list[str]
    ) -> dict[str, Any]:
        if not peer_ids or self.pings_service is None:
            return {}
        unique_peer_ids = list(dict.fromkeys(peer_ids))
        states = await asyncio.gather(
            *(
                self.pings_service.get_contact_state(
                    viewer_user_id=user_id, peer_user_id=peer_id
                )
                for peer_id in unique_peer_ids
            )
        )
        return dict(zip(unique_peer_ids, states))

    def _conversation_user_summary(
        self,
        *,
        user_id: str,
        user: Any | None,
        is_online: bool,
        contact_state: Any | None = None,
    ) -> ConversationUserSummary:
        return ConversationUserSummary(
            id=user_id,
            username=self._user_value(user, "username") if user is not None else None,
            display_name=(
                self._user_value(user, "display_name") if user is not None else None
            ),
            avatar=(
                build_user_avatar_payload(self._user_value(user, "avatar"))
                if user is not None
                else None
            ),
            is_online=is_online,
            can_ping=getattr(contact_state, "can_ping", None),
            chat_allowed=getattr(contact_state, "chat_allowed", None),
            ping_status=getattr(contact_state, "ping_status", None),
            is_ghost=user is None,
        )

    def _user_value(self, user: Any, field: str, default: Any = None) -> Any:
        if user is None:
            return default
        if isinstance(user, dict):
            return user.get(field, default)
        return getattr(user, field, default)

    async def get_dm_peer(self, *, user_id: str, conversation_id: str) -> str:
        """Return the other DM participant, enforcing membership and DM-only.

        Used by the conversation-scoped message endpoints to delegate to the
        pair-keyed messages service.
        """
        conversation = await self.repo.get_for_participant(
            conversation_id=conversation_id, user_id=user_id
        )
        if conversation is None:
            raise AppError(
                code="CONVERSATION_NOT_FOUND",
                message="Conversation not found",
                status_code=404,
            )
        peer_id = self._peer_id(conversation, user_id=user_id)
        if conversation.type != "dm" or peer_id is None:
            raise AppError(
                code="CONVERSATION_NOT_DM",
                message="Conversation is not a direct message",
                status_code=400,
            )
        return peer_id
