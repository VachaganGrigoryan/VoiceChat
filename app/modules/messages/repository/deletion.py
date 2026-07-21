from __future__ import annotations

from datetime import UTC, datetime

from pymongo import ReturnDocument

from app.core.errors import AppError
from app.db.models import MessageDocument
from app.db.object_id import parse_object_id as _oid


class DeletionRepositoryMixin:
    async def edit_text_message(
        self,
        *,
        message_id: str,
        sender_id: str,
        text: str,
    ) -> MessageDocument:
        existing = await MessageDocument.find_one(
            {
                "_id": _oid(message_id),
                "sender_id": sender_id,
                "type": "text",
            }
        )
        if existing is None:
            raise AppError(
                code="MESSAGE_NOT_EDITABLE",
                message="Message cannot be edited",
                status_code=400,
            )
        now = datetime.now(UTC)
        update: dict = {"edited_at": now, "updated_at": now}
        if existing.content is not None and existing.content.plaintext is not None:
            update["content.plaintext.text"] = text
        return await existing.set(update)

    async def hard_delete_owned_message(
        self,
        *,
        message_id: str,
        sender_id: str,
    ) -> MessageDocument:
        existing = await MessageDocument.find_one(
            {
                "_id": _oid(message_id),
                "sender_id": sender_id,
            }
        )
        if existing is None:
            raise AppError(
                code="MESSAGE_NOT_FOUND", message="Message not found", status_code=404
            )

        await existing.delete()

        now = datetime.now(UTC)
        await self.col.update_many(
            {"reply_to_message_id": message_id},
            {
                "$set": {
                    "reply_preview.text": None,
                    "reply_preview.is_deleted": True,
                    "updated_at": now,
                }
            },
        )
        return existing

    async def bulk_hard_delete_own_messages_in_conversation(
        self,
        *,
        conversation_id: str,
        user_id: str,
    ) -> list[MessageDocument]:
        owned = await self.col.find(
            {
                "conversation_id": conversation_id,
                "sender_id": user_id,
            }
        ).to_list(length=None)

        if not owned:
            return []

        owned_ids = [doc["_id"] for doc in owned]
        await self.col.delete_many({"_id": {"$in": owned_ids}})

        owned_id_strs = [str(oid) for oid in owned_ids]
        now = datetime.now(UTC)
        await self.col.update_many(
            {"reply_to_message_id": {"$in": owned_id_strs}},
            {
                "$set": {
                    "reply_preview.text": None,
                    "reply_preview.is_deleted": True,
                    "updated_at": now,
                }
            },
        )
        return [MessageDocument.model_validate(doc) for doc in owned]

    async def bulk_hard_delete_all_messages_in_conversation(
        self,
        *,
        conversation_id: str,
    ) -> list[MessageDocument]:
        """Hard-delete every message in a conversation, returning the removed docs.

        Used by the owner/admin "clear history for everyone" action; the returned
        documents let the caller clean up any associated media objects.
        """
        docs = await self.col.find(
            {"conversation_id": conversation_id}
        ).to_list(length=None)

        if not docs:
            return []

        message_ids = [doc["_id"] for doc in docs]
        await self.col.delete_many({"_id": {"$in": message_ids}})
        return [MessageDocument.model_validate(doc) for doc in docs]

    async def hide_peer_messages_for_user(
        self,
        *,
        conversation_id: str,
        user_id: str,
    ) -> int:
        now = datetime.now(UTC)
        result = await self.col.update_many(
            {
                "conversation_id": conversation_id,
                "sender_id": {"$ne": user_id},
                "hidden_for_user_ids": {"$ne": user_id},
            },
            {
                "$addToSet": {"hidden_for_user_ids": user_id},
                "$set": {"updated_at": now},
            },
        )
        return result.modified_count

    async def hide_message_for_user(
        self,
        *,
        message_id: str,
        user_id: str,
    ) -> MessageDocument:
        now = datetime.now(UTC)
        res = await self.col.find_one_and_update(
            {
                "_id": _oid(message_id),
                "hidden_for_user_ids": {"$ne": user_id},
            },
            {
                "$addToSet": {"hidden_for_user_ids": user_id},
                "$set": {"updated_at": now},
            },
            return_document=ReturnDocument.AFTER,
        )
        if res is None:
            raise AppError(
                code="MESSAGE_NOT_FOUND", message="Message not found", status_code=404
            )
        return MessageDocument.model_validate(res)
