from __future__ import annotations

from app.core.errors import AppError
from app.infra.storage import get_storage
from app.modules.messages.repository import conversation_id_for
from app.modules.messages.schemas import (
    DeleteMessageResponse,
    MessageDeleteOutcome,
)
from app.modules.messages.service.base import _media_dict


class DeleteMessagesMixin:
    async def delete_message(self, *, message_id: str, actor_user_id: str):
        existing = await self.repo.get_by_id(message_id=message_id)
        if not existing:
            raise AppError(
                code="MESSAGE_NOT_FOUND", message="Message not found", status_code=404
            )

        owner_user_id = str(existing.sender_id)
        receiver_user_id = str(existing.receiver_id)
        if actor_user_id not in {owner_user_id, receiver_user_id}:
            raise AppError(
                code="MESSAGE_NOT_FOUND", message="Message not found", status_code=404
            )

        if actor_user_id == owner_user_id:
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
                    receiver_id=receiver_user_id,
                )

            deleted = await self.repo.hard_delete_owned_message(
                message_id=message_id,
                sender_id=actor_user_id,
            )

            media = _media_dict(deleted.media)
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
                receiver_id=receiver_user_id,
            )

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
            receiver_id=receiver_user_id,
        )

    async def _clear_conversation_for_user(
        self, *, user_id: str, peer_user_id: str
    ) -> tuple[str, int]:
        """
        Hard-deletes own messages (with media cleanup) and soft-hides peer messages.
        Returns (conversation_id, total_affected).
        """
        conv_id = conversation_id_for(user_id, peer_user_id)

        deleted_docs = await self.repo.bulk_hard_delete_own_messages_in_conversation(
            user_id=user_id,
            peer_user_id=peer_user_id,
        )
        for doc in deleted_docs:
            media = _media_dict(doc.media)
            if media and media.get("key") and media.get("storage"):
                await get_storage(media["storage"]).delete(media["key"])

        hidden_count = await self.repo.hide_peer_messages_for_user(
            user_id=user_id,
            peer_user_id=peer_user_id,
        )

        return conv_id, len(deleted_docs) + hidden_count

    async def clear_chat_history(
        self, *, user_id: str, peer_user_id: str
    ) -> tuple[str, int]:
        return await self._clear_conversation_for_user(
            user_id=user_id,
            peer_user_id=peer_user_id,
        )

    async def delete_chat(
        self, *, user_id: str, peer_user_id: str
    ) -> tuple[str, int, bool]:
        conv_id, count = await self._clear_conversation_for_user(
            user_id=user_id,
            peer_user_id=peer_user_id,
        )
        ping_deleted = False
        if self.pings_service is not None:
            ping_deleted = await self.pings_service.delete_ping_for_pair(
                user_id=user_id,
                peer_user_id=peer_user_id,
            )
        return conv_id, count, ping_deleted
