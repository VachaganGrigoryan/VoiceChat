from __future__ import annotations

from app.core.errors import AppError
from app.db.models import MessageContainerType
from app.infra.storage import get_storage
from app.modules.authorization.permissions import MESSAGE_DELETE_OWN
from app.modules.messages.repository.mappers import message_media
from app.modules.messages.schemas import (
    DeleteMessageResponse,
    MessageDeleteOutcome,
)
from app.modules.messages.service.base import _media_dict


class DeleteMessagesMixin:
    async def delete_message(
        self,
        *,
        container_type: MessageContainerType,
        container_id: str,
        message_id: str,
        actor_user_id: str,
    ):
        existing = await self.repo.get_by_id_in_container(
            container_type=container_type,
            container_id=container_id,
            message_id=message_id,
            user_id=actor_user_id,
        )
        if not existing:
            raise AppError(
                code="MESSAGE_NOT_FOUND", message="Message not found", status_code=404
            )

        owner_user_id = str(existing.sender_id)
        # Hiding a message is a per-user view change, not a deletion, so it needs
        # no delete right; removing it for everyone does (§91).
        if actor_user_id != owner_user_id or existing.type == "call":
            hidden = await self.repo.hide_message_for_user(
                message_id=message_id,
                user_id=actor_user_id,
            )
            return MessageDeleteOutcome(
                response=DeleteMessageResponse(
                    message_id=message_id,
                    container_type=hidden.container_type,
                    container_id=hidden.container_id,
                    actor_user_id=actor_user_id,
                    deleted_for_everyone=False,
                    hidden_for_me=True,
                    deleted_media=False,
                ),
                sender_id=owner_user_id,
            )

        await self._require_container_permission(
            user_id=actor_user_id,
            action=MESSAGE_DELETE_OWN,
            container_type=container_type,
            container_id=container_id,
            sender_id=owner_user_id,
            message="Not allowed to delete this message",
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
                container_type=deleted.container_type,
                container_id=deleted.container_id,
                actor_user_id=actor_user_id,
                deleted_for_everyone=True,
                hidden_for_me=False,
                deleted_media=deleted_media,
            ),
            sender_id=owner_user_id,
        )

    async def _clear_container_for_user(
        self,
        *,
        container_type: MessageContainerType,
        container_id: str,
        user_id: str,
    ) -> tuple[str, int]:
        """
        Hard-deletes own messages (with media cleanup) and soft-hides peer messages.
        Returns (container_id, total_affected).
        """
        deleted_docs = await self.repo.bulk_hard_delete_own_messages_in_container(
            container_type=container_type,
            container_id=container_id,
            user_id=user_id,
        )
        for doc in deleted_docs:
            media = _media_dict(message_media(doc))
            if media and media.get("key") and media.get("storage"):
                await get_storage(media["storage"]).delete(media["key"])

        hidden_count = await self.repo.hide_peer_messages_for_user(
            container_type=container_type,
            container_id=container_id,
            user_id=user_id,
        )

        return container_id, len(deleted_docs) + hidden_count

    async def clear_chat_history(
        self,
        *,
        container_type: MessageContainerType,
        container_id: str,
        user_id: str,
    ) -> tuple[str, int]:
        return await self._clear_container_for_user(
            container_type=container_type,
            container_id=container_id,
            user_id=user_id,
        )

    async def clear_chat_history_for_everyone(
        self,
        *,
        container_type: MessageContainerType,
        container_id: str,
    ) -> tuple[str, int]:
        """Hard-delete every message (and its media) in a container for all users.

        Authorization (owner/admin, group-only) is enforced by the caller via the
        conversations service; this method performs the irreversible deletion.
        """
        deleted_docs = await self.repo.bulk_hard_delete_all_messages_in_container(
            container_type=container_type,
            container_id=container_id,
        )
        for doc in deleted_docs:
            media = _media_dict(message_media(doc))
            if media and media.get("key") and media.get("storage"):
                await get_storage(media["storage"]).delete(media["key"])

        return container_id, len(deleted_docs)

    async def delete_chat(
        self,
        *,
        container_type: MessageContainerType,
        container_id: str,
        user_id: str,
    ) -> tuple[str, int]:
        return await self._clear_container_for_user(
            container_type=container_type,
            container_id=container_id,
            user_id=user_id,
        )
