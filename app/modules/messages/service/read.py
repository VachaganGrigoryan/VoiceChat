from __future__ import annotations

from typing import Optional

from app.core.errors import AppError
from app.db.models import MessageContainerType
from app.modules.messages.repository.mappers import (
    to_message_doc,
    to_thread_summary,
)
from app.modules.messages.schemas import (
    MessageDoc,
    ThreadSummary,
)


class ReadMessagesMixin:
    async def search_messages(
        self,
        *,
        user_id: str,
        query: str,
        limit: int = 20,
        page: int = 1,
    ) -> tuple[list[MessageDoc], bool]:
        normalized_query = (query or "").strip()
        if not normalized_query:
            raise AppError(
                code="INVALID_SEARCH_QUERY",
                message="query is required",
                status_code=400,
            )
        if limit < 1 or limit > 100:
            raise AppError(
                code="INVALID_LIMIT",
                message="limit must be between 1 and 100",
                status_code=400,
            )
        if page < 1:
            raise AppError(
                code="INVALID_PAGE",
                message="page must be greater than or equal to 1",
                status_code=400,
            )
        if self.conversations_service is None:
            return [], False
        conversation_ids = (
            await self.conversations_service.accessible_conversation_ids(
                user_id=user_id
            )
        )
        docs, has_more = await self.repo.search_messages(
            container_type="conversation",
            container_ids=conversation_ids,
            user_id=user_id,
            query=normalized_query,
            limit=limit,
            skip=(page - 1) * limit,
        )
        return [to_message_doc(doc) for doc in docs], has_more

    async def _to_message_docs_with_receipts(
        self,
        *,
        container_type: MessageContainerType,
        container_id: str,
        docs,
    ) -> list[MessageDoc]:
        summaries = await self.repo.receipt_summaries_for_messages(
            container_type=container_type,
            container_id=container_id,
            messages=list(docs),
        )
        return [
            to_message_doc(doc, receipt_summary=summaries.get(doc.str_id))
            for doc in docs
        ]

    async def get_messages_by_ids(
        self,
        *,
        container_type: MessageContainerType,
        container_id: str,
        message_ids: list[str],
        user_id: str,
    ) -> list[MessageDoc]:
        docs = await self.repo.list_by_ids_in_container(
            container_type=container_type,
            container_id=container_id,
            message_ids=message_ids,
            user_id=user_id,
        )
        return await self._to_message_docs_with_receipts(
            container_type=container_type, container_id=container_id, docs=docs
        )

    async def get_message(
        self,
        *,
        container_type: MessageContainerType,
        container_id: str,
        message_id: str,
        user_id: str,
    ) -> MessageDoc:
        doc = await self.repo.get_by_id_in_container(
            container_type=container_type,
            container_id=container_id,
            message_id=message_id,
            user_id=user_id,
        )
        if doc is None:
            raise AppError(
                code="MESSAGE_NOT_FOUND", message="Message not found", status_code=404
            )
        docs = await self._to_message_docs_with_receipts(
            container_type=container_type, container_id=container_id, docs=[doc]
        )
        return docs[0]

    async def get_history(
        self,
        *,
        container_type: MessageContainerType,
        container_id: str,
        user_id: str,
        limit: int = 20,
        cursor: Optional[str] = None,
    ):
        docs, next_cursor = await self.repo.list_history_for_container(
            container_type=container_type,
            container_id=container_id,
            user_id=user_id,
            limit=limit,
            cursor=cursor,
        )
        return (
            await self._to_message_docs_with_receipts(
                container_type=container_type,
                container_id=container_id,
                docs=docs,
            ),
            next_cursor,
        )

    async def get_thread(
        self,
        *,
        container_type: MessageContainerType,
        container_id: str,
        message_id: str,
        user_id: str,
    ) -> list[MessageDoc]:
        """The flat item pool of a thread — a channel's is its comments (§35)."""
        docs = await self.repo.load_thread_messages(
            container_type=container_type,
            container_id=container_id,
            message_id=message_id,
            user_id=user_id,
        )
        return await self._to_message_docs_with_receipts(
            container_type=container_type,
            container_id=container_id,
            docs=docs,
        )

    async def get_thread_summary(
        self,
        *,
        container_type: MessageContainerType,
        container_id: str,
        message_id: str,
        user_id: str,
    ) -> ThreadSummary:
        doc = await self.repo.load_thread_summary(
            container_type=container_type,
            container_id=container_id,
            message_id=message_id,
            user_id=user_id,
        )
        return to_thread_summary(doc)
