from __future__ import annotations

from app.core.errors import AppError
from app.db.models import MessageContainerType, MessageDocument
from app.db.object_id import parse_object_id as _oid

THREAD_MESSAGES_MAX = 500


class ThreadsRepositoryMixin:
    """Reply/thread topology within a single container (§32–36).

    Every lookup here is container-scoped: a reply target, a thread root, and
    every item in a thread share one `container_type`/`container_id`, so a
    cross-container reference is rejected rather than silently resolved.
    """

    async def _load_reply_target_in_container(
        self,
        *,
        container_type: MessageContainerType,
        container_id: str,
        reply_to_message_id: str,
    ) -> MessageDocument:
        target = await MessageDocument.get(_oid(reply_to_message_id))
        if not target:
            raise AppError(
                code="MESSAGE_NOT_FOUND", message="Message not found", status_code=404
            )
        if (
            target.container_type != container_type
            or target.container_id != container_id
        ):
            raise AppError(
                code="INVALID_REPLY_TARGET",
                message="Reply target must belong to the same container",
                status_code=400,
            )
        return target

    async def _load_thread_root_in_container(
        self,
        *,
        container_type: MessageContainerType,
        container_id: str,
        thread_root_id: str,
    ) -> MessageDocument:
        """The root a thread hangs off: a root message in the same container (§97)."""
        root = await MessageDocument.get(_oid(thread_root_id))
        if root is None:
            raise AppError(
                code="THREAD_ROOT_NOT_FOUND",
                message="Thread root not found",
                status_code=404,
            )
        if root.container_type != container_type or root.container_id != container_id:
            raise AppError(
                code="INVALID_THREAD_ROOT",
                message="Thread root must belong to the same container",
                status_code=400,
            )
        if root.thread_root_id is not None:
            raise AppError(
                code="INVALID_THREAD_ROOT",
                message="Thread root must be a root message",
                status_code=400,
            )
        return root

    async def load_thread_messages(
        self,
        *,
        container_type: MessageContainerType,
        container_id: str,
        message_id: str,
        user_id: str,
    ) -> list[MessageDocument]:
        target = await self._load_reply_target_in_container(
            container_type=container_type,
            container_id=container_id,
            reply_to_message_id=message_id,
        )
        thread_root_id = target.thread_root_id or target.str_id
        cur = (
            self.col.find(
                {
                    "container_type": container_type,
                    "container_id": container_id,
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
        container_type: MessageContainerType,
        container_id: str,
        message_id: str,
        user_id: str,
    ) -> MessageDocument:
        target = await self._load_reply_target_in_container(
            container_type=container_type,
            container_id=container_id,
            reply_to_message_id=message_id,
        )
        thread_root_id = target.thread_root_id or target.str_id
        root = await self.col.find_one(
            {
                "_id": _oid(thread_root_id),
                "container_type": container_type,
                "container_id": container_id,
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
