from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import Any

from app.core.errors import AppError
from app.db.models import ConversationDocument
from app.modules.conversations.repository.mappers import to_conversation_view
from app.modules.conversations.schemas import ConversationUserSummary, ConversationView
from app.modules.conversations.service.base import BaseConversationsService
from app.modules.realtime.presence import PresenceState
from app.modules.users.avatar import build_user_avatar_payload


class ReadConversationsMixin(BaseConversationsService):
    async def list_for_user(
        self,
        *,
        user_id: str,
        limit: int,
        cursor: str | None,
        archived: bool = False,
        folder: str | None = None,
        conversation_types: Sequence[str] | None = ("dm", "group", "channel"),
    ) -> tuple[list[ConversationDocument], str | None]:
        return await self.repo.list_for_user(
            user_id=user_id,
            limit=limit,
            cursor=cursor,
            archived=archived,
            folder=folder,
            conversation_types=conversation_types,
        )

    async def list_threads_for_user(
        self,
        *,
        user_id: str,
        limit: int,
        cursor: str | None,
        archived: bool = False,
    ) -> tuple[list[ConversationDocument], str | None]:
        return await self.repo.list_for_user(
            user_id=user_id,
            limit=limit,
            cursor=cursor,
            archived=archived,
            conversation_types=("thread",),
        )

    async def get_conversation_view(
        self, *, user_id: str, conversation_id: str
    ) -> ConversationView:
        """Return one conversation as the caller's view, with their inbox flags.

        Enforces membership. Lets clients resolve a conversation that isn't on the
        active inbox page (e.g. an archived chat) so its history stays reachable.
        """
        conversation = await self.require_participant(
            user_id=user_id, conversation_id=conversation_id
        )
        views = await self.views_for_user(
            user_id=user_id, conversations=[conversation]
        )
        return views[0]

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

    async def require_can_post(
        self, *, user_id: str, conversation_id: str
    ) -> ConversationDocument:
        """Assert the caller may post to the conversation.

        Shared gate for REST and socket send paths: enforces membership and the
        conversation's ``posting_policy``. When ``posting_policy == "admins"``
        (broadcast channels) only ``owner``/``admin`` participants may post;
        ``member``/``subscriber`` are read-only.
        """
        conversation = await self.require_participant(
            user_id=user_id, conversation_id=conversation_id
        )
        if conversation.type == "thread" and conversation.settings.get("locked_at"):
            raise AppError(
                code="THREAD_LOCKED",
                message="This thread was converted to a group and is locked",
                status_code=409,
            )
        if conversation.type == "dm":
            peer_id = self._peer_id(conversation, user_id=user_id)
            if peer_id is not None:
                await self._ensure_can_message(sender_id=user_id, receiver_id=peer_id)
        if conversation.posting_policy != "admins":
            return conversation

        participant = await self.repo.get_participant(
            conversation_id=conversation.str_id, user_id=user_id
        )
        if participant is None or participant.role not in {"owner", "admin"}:
            raise AppError(
                code="CONVERSATION_POST_FORBIDDEN",
                message="Only admins can post to this conversation",
                status_code=403,
            )
        return conversation

    async def accessible_conversation_ids(self, *, user_id: str) -> list[str]:
        return await self.repo.accessible_conversation_ids(user_id=user_id)

    async def conversation_participant_ids(
        self, *, conversation_id: str
    ) -> list[str]:
        conversation = await self.repo.get_by_id(conversation_id)
        if conversation is None:
            return []
        return [str(participant_id) for participant_id in conversation.participant_ids]

    async def mention_targets_for_conversation(
        self, *, conversation_id: str
    ) -> dict[str, str]:
        conversation = await self.repo.get_by_id(conversation_id)
        if conversation is None or self.users_repo is None:
            return {}

        participant_ids = [str(user_id) for user_id in conversation.participant_ids]
        users = await self.users_repo.find_by_ids(participant_ids)
        targets: dict[str, str] = {}
        for user_id, user in users.items():
            username = self._user_value(user, "username")
            if isinstance(username, str) and username.strip():
                targets[username.lower()] = user_id
        return targets

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
        presence_by_id = await self._presence_by_id(participant_ids)
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
                    presence_state=presence_by_id.get(str(participant_id), "offline"),
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
                    presence_state=presence_by_id.get(peer_id, "offline"),
                    contact_state=contact_state_by_peer.get(peer_id),
                )
                if peer_id is not None
                else None
            )
            viewer_participant = await self.repo.get_participant(
                conversation_id=conversation.str_id, user_id=user_id
            )
            unread_count = await self.repo.unread_count(
                message_conversation_id=conversation.str_id,
                user_id=user_id,
                last_read_at=(
                    viewer_participant.last_read_at
                    if viewer_participant is not None
                    else None
                ),
            )
            views.append(
                to_conversation_view(
                    conversation,
                    unread_count=unread_count,
                    peer_user=peer_user,
                    participant_users=participant_users,
                    viewer_participant=viewer_participant,
                )
            )
        return views

    async def _presence_by_id(self, user_ids: list[str]) -> dict[str, PresenceState]:
        if not user_ids or self.presence_service is None:
            return {}
        statuses = await asyncio.gather(
            *(self.presence_service.get_state(user_id) for user_id in user_ids)
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
        presence_state: PresenceState,
        contact_state: Any | None = None,
    ) -> ConversationUserSummary:
        has_presence_access = (
            contact_state is None
            or (getattr(contact_state, "chat_allowed", False) and not getattr(contact_state, "blocks_me", False))
        )
        exposed_presence_state: PresenceState = (
            presence_state if has_presence_access else "offline"
        )
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
            is_online=exposed_presence_state != "offline",
            presence_state=exposed_presence_state,
            last_seen_at=(
                self._user_value(user, "last_seen_at")
                if exposed_presence_state in {"away", "offline"}
                else None
            ),
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
