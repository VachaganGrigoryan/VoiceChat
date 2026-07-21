from __future__ import annotations

from app.db.models import MediaDocument
from app.infra.storage import get_storage
from app.modules.messages.media_policy import resolve_media_policy
from app.modules.messages.repository.mappers import (
    message_text,
    to_message_doc,
    to_thread_summary,
)
from app.modules.messages.schemas import ReplyMode
from app.modules.messages.service.base import SendMessageResult
from fastapi import UploadFile


class CreateMessagesMixin:
    async def upload_media_to_conversation(
        self,
        *,
        conversation_id: str,
        sender_id: str,
        message_type: str,
        media_kind: str | None,
        file: UploadFile,
        text: str | None = None,
        duration_ms: int | None = None,
        reply_mode: ReplyMode | None = None,
        reply_to_message_id: str | None = None,
    ) -> SendMessageResult:
        policy = resolve_media_policy(
            message_type=message_type,
            media_kind=media_kind,
        )
        stored = await self._store_media(
            sender_id=sender_id,
            file=file,
            allowed_mime=set(policy.allowed_mime),
            max_bytes=policy.max_bytes,
            folder=policy.folder,
        )
        normalized_reply_mode, normalized_reply_to_message_id = (
            self._normalize_reply_fields(
                reply_mode=reply_mode,
                reply_to_message_id=reply_to_message_id,
            )
        )
        try:
            return await self._create_conversation_message(
                conversation_id=conversation_id,
                sender_id=sender_id,
                message_type=message_type,
                text=self._normalize_optional_text(text),
                media=self._build_media_meta(
                    stored=stored,
                    media_kind=policy.media_kind,
                    duration_ms=self._normalize_duration_ms(duration_ms),
                ),
                reply_mode=normalized_reply_mode,
                reply_to_message_id=normalized_reply_to_message_id,
            )
        except Exception:
            await get_storage(stored.storage).delete(stored.key)
            raise

    async def send_text_to_conversation(
        self,
        *,
        conversation_id: str,
        sender_id: str,
        text: str,
        reply_mode: ReplyMode | None = None,
        reply_to_message_id: str | None = None,
    ) -> SendMessageResult:
        normalized_reply_mode, normalized_reply_to_message_id = (
            self._normalize_reply_fields(
                reply_mode=reply_mode,
                reply_to_message_id=reply_to_message_id,
            )
        )
        return await self._create_conversation_message(
            conversation_id=conversation_id,
            sender_id=sender_id,
            message_type="text",
            text=self._normalize_text(text),
            reply_mode=normalized_reply_mode,
            reply_to_message_id=normalized_reply_to_message_id,
        )

    async def _create_conversation_message(
        self,
        *,
        conversation_id: str,
        sender_id: str,
        message_type: str,
        text: str | None = None,
        media: MediaDocument | None = None,
        reply_mode: ReplyMode | None = None,
        reply_to_message_id: str | None = None,
    ) -> SendMessageResult:
        thread_summary = None

        if reply_mode == "quote":
            doc = await self.repo.create_conversation_quote_reply(
                conversation_id=conversation_id,
                sender_id=sender_id,
                message_type=message_type,
                text=text,
                media=media,
                reply_to_message_id=reply_to_message_id,
            )
        elif reply_mode == "thread":
            doc = await self.repo.create_conversation_thread_reply(
                conversation_id=conversation_id,
                sender_id=sender_id,
                message_type=message_type,
                text=text,
                media=media,
                reply_to_message_id=reply_to_message_id,
            )
            summary_doc = await self.repo.load_thread_summary_for_conversation(
                conversation_id=conversation_id,
                message_id=doc.thread_root_id or "",
                user_id=sender_id,
            )
            thread_summary = to_thread_summary(summary_doc)
        else:
            doc = await self.repo.create_conversation_message(
                conversation_id=conversation_id,
                sender_id=sender_id,
                message_type=message_type,
                text=text,
                media=media,
            )

        if reply_mode != "thread":
            await self._materialize_conversation_message(doc)
        summaries = await self.repo.receipt_summaries_for_messages(
            conversation_id=conversation_id,
            messages=[doc],
        )
        return SendMessageResult(
            message=to_message_doc(doc, receipt_summary=summaries.get(doc.str_id)),
            thread_summary=thread_summary,
        )

    async def _materialize_conversation_message(self, doc) -> None:
        if self.conversations_service is None:
            return
        await self.conversations_service.materialize_conversation_message(
            conversation_id=doc.conversation_id,
            sender_id=str(doc.sender_id),
            message_id=doc.str_id,
            message_type=doc.type,
            preview_text=message_text(doc),
            created_at=doc.created_at,
        )
