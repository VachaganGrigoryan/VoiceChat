from __future__ import annotations

from app.core.errors import AppError
from app.db.models import MessageDocument
from app.db.object_id import parse_object_id as _oid

THREAD_MESSAGES_MAX = 500


class ThreadsRepositoryMixin:
    async def _load_reply_target_by_conversation(
        self, *, conversation_id: str, reply_to_message_id: str
    ) -> MessageDocument:
        target = await MessageDocument.get(_oid(reply_to_message_id))
        if not target:
            raise AppError(
                code="MESSAGE_NOT_FOUND", message="Message not found", status_code=404
            )
        if target.conversation_id != conversation_id:
            raise AppError(
                code="INVALID_REPLY_TARGET",
                message="Reply target must belong to the same conversation",
                status_code=400,
            )
        return target

    async def load_thread_messages_for_conversation(
        self, *, conversation_id: str, message_id: str, user_id: str
    ) -> list[MessageDocument]:
        target = await self._load_reply_target_by_conversation(
            conversation_id=conversation_id,
            reply_to_message_id=message_id,
        )
        thread_root_id = target.thread_root_id or target.str_id
        cur = (
            self.col.find(
                {
                    "conversation_id": conversation_id,
                    "thread_root_id": thread_root_id,
                    "hidden_for_user_ids": {"$ne": user_id},
                }
            )
            .sort([("created_at", 1), ("_id", 1)])
            .limit(THREAD_MESSAGES_MAX)
        )
        return [
            MessageDocument.model_validate(doc)
            for doc in await cur.to_list(length=THREAD_MESSAGES_MAX)
        ]

    async def load_thread_summary_for_conversation(
        self, *, conversation_id: str, message_id: str, user_id: str
    ) -> MessageDocument:
        target = await self._load_reply_target_by_conversation(
            conversation_id=conversation_id,
            reply_to_message_id=message_id,
        )
        thread_root_id = target.thread_root_id or target.str_id
        root = await self.col.find_one(
            {
                "_id": _oid(thread_root_id),
                "conversation_id": conversation_id,
                "hidden_for_user_ids": {"$ne": user_id},
            }
        )
        if not root:
            raise AppError(
                code="THREAD_ROOT_NOT_FOUND",
                message="Thread root not found",
                status_code=404,
            )
        return MessageDocument.model_validate(root)
