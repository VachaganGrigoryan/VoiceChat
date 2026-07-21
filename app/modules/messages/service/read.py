from __future__ import annotations

from typing import Optional

from app.modules.messages.repository.mappers import (
    to_message_doc,
    to_thread_summary,
)
from app.modules.messages.schemas import (
    MessageDoc,
    ThreadSummary,
)


class ReadMessagesMixin:
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
