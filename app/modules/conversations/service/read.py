from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import Any

from app.core.errors import AppError
from app.modules.authorization.permissions import MESSAGE_CREATE, POLL_MANAGE
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
        conversation_types: Sequence[str] | None = ("dm", "group"),
        space_id: str | None = None,
    ) -> tuple[list[ConversationDocument], str | None]:
        if space_id is not None:
            from app.modules.spaces.repository import find_active_space_membership
            member = await find_active_space_membership(
                space_id=str(space_id), user_id=str(user_id)
            )
            if member is None:
                raise AppError(
                    code="SPACE_FORBIDDEN",
                    message="Not a member of this space",
                    status_code=403,
                )

        return await self.repo.list_for_user(
            user_id=user_id,
            limit=limit,
            cursor=cursor,
            archived=archived,
            folder=folder,
            conversation_types=conversation_types,
            space_id=space_id,
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

        Shared gate for REST and socket send paths: enforces membership, the DM
        ping rule, and then `message.create` — which is where the conversation's
        ``posting_policy`` is weighed against the caller's standing (§59).
        """
        conversation = await self.require_participant(
            user_id=user_id, conversation_id=conversation_id
        )
        if conversation.type == "dm":
            peer_id = self._peer_id(conversation, user_id=user_id)
            if peer_id is not None:
                await self._ensure_can_message(sender_id=user_id, receiver_id=peer_id)
        # `posting_policy` is resource policy; `can` weighs it against the
        # caller's ownership, overrides, and roles in one place (§59).
        allowed = await self.authorization.can(
            user_id, MESSAGE_CREATE, "conversation", conversation.str_id
        )
        if not allowed:
            raise AppError(
                code="CONVERSATION_POST_FORBIDDEN",
                message="Not allowed to post to this conversation",
                status_code=403,
            )
        return conversation

    async def require_can_create_poll(
        self, *, user_id: str, conversation_id: str
    ) -> ConversationDocument:
        """Assert the caller may create a poll in the conversation.

        Builds on ``require_can_post`` (membership + posting policy), then adds
        the poll-specific rule: in group conversations the caller
        needs `poll.create`, unless ``settings.allow_member_polls`` is set. A
        ``dm`` allows any participant.
        """
        conversation = await self.require_can_post(
            user_id=user_id, conversation_id=conversation_id
        )
        if conversation.type != "group":
            return conversation
        if conversation.settings.get("allow_member_polls"):
            return conversation

        allowed = await self.authorization.can(
            user_id, POLL_MANAGE, "conversation", conversation.str_id
        )
        if not allowed:
            raise AppError(
                code="POLL_CREATE_FORBIDDEN",
                message="Not allowed to create polls in this conversation",
                status_code=403,
            )
        return conversation

    async def get_participant_role(
        self, *, user_id: str, conversation_id: str
    ) -> str | None:
        """The name of the role the caller holds here, or None."""
        participant = await self.repo.get_participant(
            conversation_id=conversation_id, user_id=user_id
        )
        return participant.role if participant is not None else None

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
