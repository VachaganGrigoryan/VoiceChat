from __future__ import annotations

from datetime import UTC, datetime

from pymongo import ReturnDocument

from app.core.errors import AppError
from app.db.models import MessageDocument
from app.db.object_id import parse_object_id as _oid
from app.modules.messages.repository.helpers import conversation_id_for


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
        return await existing.set({"text": text, "edited_at": now, "updated_at": now})

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
        user_id: str,
        peer_user_id: str,
    ) -> list[MessageDocument]:
        conv_id = conversation_id_for(user_id, peer_user_id)
        owned = await self.col.find(
            {
                "conversation_id": conv_id,
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

    async def hide_peer_messages_for_user(
        self,
        *,
        user_id: str,
        peer_user_id: str,
    ) -> int:
        conv_id = conversation_id_for(user_id, peer_user_id)
        now = datetime.now(UTC)
        result = await self.col.update_many(
            {
                "conversation_id": conv_id,
                "sender_id": peer_user_id,
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
                "$or": [
                    {"sender_id": user_id},
                    {"receiver_id": user_id},
                ],
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
