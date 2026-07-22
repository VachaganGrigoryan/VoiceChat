from __future__ import annotations

from typing import Optional

from app.core.errors import AppError
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
            conversation_ids=conversation_ids,
            user_id=user_id,
            query=normalized_query,
            limit=limit,
            skip=(page - 1) * limit,
        )
        return [to_message_doc(doc) for doc in docs], has_more

    async def _to_message_docs_with_receipts(
        self, *, conversation_id: str, docs
    ) -> list[MessageDoc]:
        summaries = await self.repo.receipt_summaries_for_messages(
            conversation_id=conversation_id,
            messages=list(docs),
        )
        return [
            to_message_doc(doc, receipt_summary=summaries.get(doc.str_id))
            for doc in docs
        ]

    async def get_messages_by_ids_for_conversation(
        self, *, conversation_id: str, message_ids: list[str], user_id: str
    ) -> list[MessageDoc]:
        docs = await self.repo.list_by_ids_for_conversation(
            conversation_id=conversation_id,
            message_ids=message_ids,
            user_id=user_id,
        )
        return await self._to_message_docs_with_receipts(
            conversation_id=conversation_id, docs=docs
        )

    async def get_conversation_history(
        self,
        *,
        conversation_id: str,
        user_id: str,
        limit: int = 20,
        cursor: Optional[str] = None,
    ):
        docs, next_cursor = await self.repo.list_history_for_conversation(
            conversation_id=conversation_id,
            user_id=user_id,
            limit=limit,
            cursor=cursor,
        )
        return (
            await self._to_message_docs_with_receipts(
                conversation_id=conversation_id,
                docs=docs,
            ),
            next_cursor,
        )

    async def get_thread_for_conversation(
        self,
        *,
        conversation_id: str,
        message_id: str,
        user_id: str,
    ) -> list[MessageDoc]:
        docs = await self.repo.load_thread_messages_for_conversation(
            conversation_id=conversation_id,
            message_id=message_id,
            user_id=user_id,
        )
        return await self._to_message_docs_with_receipts(
            conversation_id=conversation_id,
            docs=docs,
        )

    async def get_thread_summary_for_conversation(
        self,
        *,
        conversation_id: str,
        message_id: str,
        user_id: str,
    ) -> ThreadSummary:
        doc = await self.repo.load_thread_summary_for_conversation(
            conversation_id=conversation_id,
            message_id=message_id,
            user_id=user_id,
        )
        return to_thread_summary(doc)
