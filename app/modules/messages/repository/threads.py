from __future__ import annotations

from datetime import datetime

from pymongo import ReturnDocument

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

    async def load_thread_bridge_messages(
        self,
        *,
        parent_conversation_id: str,
        thread_conversation_id: str,
        root_message_id: str,
        user_id: str,
        include_root: bool = False,
    ) -> tuple[list[MessageDocument], bool]:
        root = await self.get_by_id_for_conversation(
            conversation_id=parent_conversation_id,
            message_id=root_message_id,
            user_id=user_id,
        )
        if root is None:
            raise AppError(
                code="THREAD_ROOT_NOT_FOUND",
                message="Thread root not found",
                status_code=404,
            )

        legacy_raw = await (
            self.col.find(
                {
                    "conversation_id": parent_conversation_id,
                    "thread_root_id": root_message_id,
                    "hidden_for_user_ids": {"$ne": user_id},
                    "state": {"$ne": "scheduled"},
                }
            )
            .sort([("created_at", 1), ("_id", 1)])
            .limit(THREAD_MESSAGES_MAX + 1)
            .to_list(length=THREAD_MESSAGES_MAX + 1)
        )
        canonical_raw = await (
            self.col.find(
                {
                    "conversation_id": thread_conversation_id,
                    "hidden_for_user_ids": {"$ne": user_id},
                    "state": {"$ne": "scheduled"},
                }
            )
            .sort([("created_at", 1), ("_id", 1)])
            .limit(THREAD_MESSAGES_MAX + 1)
            .to_list(length=THREAD_MESSAGES_MAX + 1)
        )

        truncated = (
            len(legacy_raw) > THREAD_MESSAGES_MAX
            or len(canonical_raw) > THREAD_MESSAGES_MAX
        )
        docs = [
            MessageDocument.model_validate(item)
            for item in legacy_raw[:THREAD_MESSAGES_MAX]
        ]
        docs.extend(
            MessageDocument.model_validate(item)
            for item in canonical_raw[:THREAD_MESSAGES_MAX]
        )
        docs.sort(key=lambda item: (item.created_at, item.str_id))
        if include_root:
            docs.insert(0, root)
        return docs[:THREAD_MESSAGES_MAX], truncated or len(docs) > THREAD_MESSAGES_MAX

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

    async def bump_thread_root_summary_for_conversation(
        self,
        *,
        parent_conversation_id: str,
        root_message_id: str,
        reply_created_at: datetime,
    ) -> MessageDocument:
        root = await self.col.find_one_and_update(
            {
                "_id": _oid(root_message_id),
                "conversation_id": parent_conversation_id,
            },
            {
                "$set": {
                    "is_thread_root": True,
                    "last_thread_reply_at": reply_created_at,
                    "updated_at": reply_created_at,
                },
                "$inc": {"thread_reply_count": 1},
            },
            return_document=ReturnDocument.AFTER,
        )
        if root is None:
            raise AppError(
                code="THREAD_ROOT_NOT_FOUND",
                message="Thread root not found",
                status_code=404,
            )
        return MessageDocument.model_validate(root)
