from __future__ import annotations

from app.core.errors import AppError
from app.infra.storage import get_storage
from app.modules.messages.repository.mappers import message_media
from app.modules.messages.schemas import (
    DeleteMessageResponse,
    MessageDeleteOutcome,
)
from app.modules.messages.service.base import _media_dict


class DeleteMessagesMixin:
    async def delete_message_for_conversation(
        self, *, conversation_id: str, message_id: str, actor_user_id: str
    ):
        existing = await self.repo.get_by_id_for_conversation(
            conversation_id=conversation_id,
            message_id=message_id,
            user_id=actor_user_id,
        )
        if not existing:
            raise AppError(
                code="MESSAGE_NOT_FOUND", message="Message not found", status_code=404
            )

        owner_user_id = str(existing.sender_id)
        if actor_user_id != owner_user_id:
            hidden = await self.repo.hide_message_for_user(
                message_id=message_id,
                user_id=actor_user_id,
            )
            return MessageDeleteOutcome(
                response=DeleteMessageResponse(
                    message_id=message_id,
                    conversation_id=hidden.conversation_id,
                    actor_user_id=actor_user_id,
                    deleted_for_everyone=False,
                    hidden_for_me=True,
                    deleted_media=False,
                ),
                sender_id=owner_user_id,
            )

        if existing.type == "call":
            hidden = await self.repo.hide_message_for_user(
                message_id=message_id,
                user_id=actor_user_id,
            )
            return MessageDeleteOutcome(
                response=DeleteMessageResponse(
                    message_id=message_id,
                    conversation_id=hidden.conversation_id,
                    actor_user_id=actor_user_id,
                    deleted_for_everyone=False,
                    hidden_for_me=True,
                    deleted_media=False,
                ),
                sender_id=owner_user_id,
            )

        deleted = await self.repo.hard_delete_owned_message(
            message_id=message_id,
            sender_id=actor_user_id,
        )
        media = _media_dict(message_media(deleted))
        deleted_media = False
        if media and media.get("key") and media.get("storage"):
            await get_storage(media["storage"]).delete(media["key"])
            deleted_media = True

        return MessageDeleteOutcome(
            response=DeleteMessageResponse(
                message_id=message_id,
                conversation_id=deleted.conversation_id,
                actor_user_id=actor_user_id,
                deleted_for_everyone=True,
                hidden_for_me=False,
                deleted_media=deleted_media,
            ),
            sender_id=owner_user_id,
        )

    async def _clear_conversation_for_user(
        self, *, conversation_id: str, user_id: str
    ) -> tuple[str, int]:
        """
        Hard-deletes own messages (with media cleanup) and soft-hides peer messages.
        Returns (conversation_id, total_affected).
        """
        deleted_docs = await self.repo.bulk_hard_delete_own_messages_in_conversation(
            conversation_id=conversation_id,
            user_id=user_id,
        )
        for doc in deleted_docs:
            media = _media_dict(message_media(doc))
            if media and media.get("key") and media.get("storage"):
                await get_storage(media["storage"]).delete(media["key"])

        hidden_count = await self.repo.hide_peer_messages_for_user(
            conversation_id=conversation_id,
            user_id=user_id,
        )

        return conversation_id, len(deleted_docs) + hidden_count

    async def clear_chat_history(
        self, *, conversation_id: str, user_id: str
    ) -> tuple[str, int]:
        return await self._clear_conversation_for_user(
            conversation_id=conversation_id,
            user_id=user_id,
        )

    async def clear_chat_history_for_everyone(
        self, *, conversation_id: str
    ) -> tuple[str, int]:
        """Hard-delete every message (and its media) in a conversation for all users.

        Authorization (owner/admin, group-only) is enforced by the caller via the
        conversations service; this method performs the irreversible deletion.
        """
        deleted_docs = await self.repo.bulk_hard_delete_all_messages_in_conversation(
            conversation_id=conversation_id,
        )
        for doc in deleted_docs:
            media = _media_dict(message_media(doc))
            if media and media.get("key") and media.get("storage"):
                await get_storage(media["storage"]).delete(media["key"])

        return conversation_id, len(deleted_docs)

    async def delete_chat(
        self, *, conversation_id: str, user_id: str, peer_user_id: str | None = None
    ) -> tuple[str, int, bool]:
        conv_id, count = await self._clear_conversation_for_user(
            conversation_id=conversation_id,
            user_id=user_id,
        )
        ping_deleted = False
        if self.pings_service is not None and peer_user_id is not None:
            ping_deleted = await self.pings_service.delete_ping_for_pair(
                user_id=user_id,
                peer_user_id=peer_user_id,
            )
        return conv_id, count, ping_deleted
