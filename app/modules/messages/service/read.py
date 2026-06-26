from __future__ import annotations

import asyncio
from typing import Optional

from app.modules.auth.repository import UsersRepository
from app.modules.messages.repository.mappers import (
    normalize_call_payload,
    normalize_message_record,
    to_message_doc,
    to_thread_summary,
)
from app.modules.messages.schemas import (
    ConversationItem,
    ConversationLastMessage,
    ConversationPeer,
    MessageDoc,
    ThreadSummary,
)
from app.modules.pings.schemas import ContactState
from app.modules.realtime.presence import get_presence_backend
from app.modules.users.avatar import build_user_avatar_payload


class ReadMessagesMixin:
    async def get_history(
        self,
        *,
        user_id: str,
        peer_user_id: str,
        limit: int = 20,
        cursor: Optional[str] = None,
    ):
        docs, next_cursor = await self.repo.list_history(
            user_id=user_id,
            peer_user_id=peer_user_id,
            limit=limit,
            cursor=cursor,
        )
        items = [to_message_doc(d) for d in docs]
        return items, next_cursor

    async def get_thread(
        self,
        *,
        message_id: str,
        user_id: str,
    ) -> list[MessageDoc]:
        docs = await self.repo.load_thread_messages(
            message_id=message_id,
            user_id=user_id,
        )
        return [to_message_doc(doc) for doc in docs]

    async def get_thread_summary(
        self,
        *,
        message_id: str,
        user_id: str,
    ) -> ThreadSummary:
        doc = await self.repo.load_thread_summary(
            message_id=message_id,
            user_id=user_id,
        )
        return to_thread_summary(doc)

    async def list_conversations(
        self,
        *,
        user_id: str,
        limit: int = 50,
        cursor: str | None = None,
    ) -> tuple[list[ConversationItem], str | None]:
        rows, next_cursor = await self.repo.list_conversations_for_user(
            user_id=user_id,
            limit=limit,
            cursor=cursor,
        )

        users_repo = UsersRepository()
        presence = get_presence_backend()

        items: list[ConversationItem] = []
        peer_user_ids = list(
            dict.fromkeys(
                (
                    str(row.last_message.receiver_id)
                    if str(row.last_message.sender_id) == user_id
                    else str(row.last_message.sender_id)
                )
                for row in rows
            )
        )

        users_task = asyncio.create_task(users_repo.find_by_ids(peer_user_ids))
        presence_task = asyncio.create_task(
            self._get_presence_map(presence=presence, user_ids=peer_user_ids)
        )
        contact_states_task = asyncio.create_task(
            self._get_contact_states(user_id=user_id, peer_user_ids=peer_user_ids)
        )
        users_by_id, online_by_id, contact_states = await asyncio.gather(
            users_task,
            presence_task,
            contact_states_task,
        )

        for row in rows:
            msg = row.last_message

            sender_id = str(msg.sender_id)
            receiver_id = str(msg.receiver_id)
            peer_user_id = receiver_id if sender_id == user_id else sender_id

            peer = users_by_id.get(peer_user_id)
            is_online = online_by_id.get(peer_user_id, False)
            contact_state = contact_states.get(
                peer_user_id,
                ContactState(can_ping=True, chat_allowed=False, ping_status="none"),
            )

            avatar = build_user_avatar_payload(peer.avatar) if peer else None

            # A conversation can only exist after an accepted ping. If chat_allowed is
            # False and ping_status is "none", the ping was deleted — show the peer as
            # a ghost so the client can display "Deleted Account" until re-pinged.
            is_ghost = (
                not contact_state.chat_allowed and contact_state.ping_status == "none"
            )

            text = msg.text
            msg_type, media = normalize_message_record(msg)
            call = normalize_call_payload(message_type=msg_type, call=msg.call)

            items.append(
                ConversationItem(
                    conversation_id=row.conversation_id,
                    peer_user=ConversationPeer(
                        id=peer_user_id,
                        username=(
                            None if is_ghost else (peer.username if peer else None)
                        ),
                        display_name=(
                            None if is_ghost else (peer.display_name if peer else None)
                        ),
                        avatar=None if is_ghost else avatar,
                        is_online=False if is_ghost else is_online,
                        can_ping=contact_state.can_ping,
                        chat_allowed=contact_state.chat_allowed,
                        ping_status=contact_state.ping_status,
                        is_ghost=is_ghost,
                    ),
                    last_message=ConversationLastMessage(
                        id=msg.str_id,
                        type=msg_type,
                        text=text,
                        media=media,
                        call=call,
                        status=msg.status,
                        created_at=msg.created_at,
                    ),
                    last_message_at=msg.created_at,
                    unread_count=row.unread_count,
                )
            )

        return items, next_cursor

    async def _get_presence_map(
        self, *, presence, user_ids: list[str]
    ) -> dict[str, bool]:
        if not user_ids:
            return {}

        statuses = await asyncio.gather(
            *(presence.is_online(user_id) for user_id in user_ids)
        )
        return dict(zip(user_ids, statuses))

    async def _get_contact_states(
        self, *, user_id: str, peer_user_ids: list[str]
    ) -> dict[str, ContactState]:
        if self.pings_service is None or not peer_user_ids:
            return {}
        return await self.pings_service.get_contact_states(
            viewer_user_id=user_id,
            peer_user_ids=peer_user_ids,
        )
