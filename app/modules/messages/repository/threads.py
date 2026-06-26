from __future__ import annotations

from app.core.errors import AppError
from app.db.models import MessageDocument
from app.db.object_id import parse_object_id as _oid
from app.modules.messages.repository.helpers import conversation_id_for, message_participants

THREAD_MESSAGES_MAX = 500


class ThreadsRepositoryMixin:
    async def _load_reply_target(
        self,
        *,
        sender_id: str,
        receiver_id: str,
        reply_to_message_id: str,
    ) -> MessageDocument:
        conv_id = conversation_id_for(sender_id, receiver_id)
        target = await MessageDocument.get(_oid(reply_to_message_id))
        if not target:
            raise AppError(
                code="MESSAGE_NOT_FOUND", message="Message not found", status_code=404
            )
        if target.conversation_id != conv_id:
            raise AppError(
                code="INVALID_REPLY_TARGET",
                message="Reply target must belong to the same conversation",
                status_code=400,
            )

        participants = message_participants(target)
        if {sender_id, receiver_id} != participants:
            raise AppError(
                code="INVALID_REPLY_TARGET",
                message="Reply target must belong to the same conversation",
                status_code=400,
            )

        return target

    async def _resolve_thread_root(
        self,
        *,
        message_id: str,
        user_id: str,
    ) -> tuple[MessageDocument, str]:
        message = await MessageDocument.find_one(
            {
                "_id": _oid(message_id),
                "hidden_for_user_ids": {"$ne": user_id},
            }
        )
        if not message:
            raise AppError(
                code="MESSAGE_NOT_FOUND", message="Message not found", status_code=404
            )
        self._assert_message_participant(message=message, user_id=user_id)

        thread_root_id = message.thread_root_id or message.str_id
        return message, thread_root_id

    async def load_thread_messages(
        self,
        *,
        message_id: str,
        user_id: str,
    ) -> list[MessageDocument]:
        message, thread_root_id = await self._resolve_thread_root(
            message_id=message_id,
            user_id=user_id,
        )
        cur = (
            self.col.find(
                {
                    "conversation_id": message.conversation_id,
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

    async def load_thread_summary(
        self,
        *,
        message_id: str,
        user_id: str,
    ) -> MessageDocument:
        _, thread_root_id = await self._resolve_thread_root(
            message_id=message_id,
            user_id=user_id,
        )
        root = await self.col.find_one(
            {
                "_id": _oid(thread_root_id),
                "hidden_for_user_ids": {"$ne": user_id},
            }
        )
        if not root:
            raise AppError(
                code="THREAD_ROOT_NOT_FOUND",
                message="Thread root not found",
                status_code=404,
            )
        root_doc = MessageDocument.model_validate(root)
        self._assert_message_participant(message=root_doc, user_id=user_id)
        return root_doc
