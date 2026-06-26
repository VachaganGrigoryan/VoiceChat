from __future__ import annotations

from app.db.models import MediaDocument
from app.infra.storage import get_storage
from app.modules.messages.media_policy import resolve_media_policy
from app.modules.messages.repository.mappers import (
    to_message_doc,
    to_thread_summary,
)
from app.modules.messages.schemas import ReplyMode
from app.modules.messages.service.base import SendMessageResult
from fastapi import UploadFile


class CreateMessagesMixin:
    async def upload_media_message(
        self,
        *,
        sender_id: str,
        receiver_id: str,
        message_type: str,
        media_kind: str | None,
        file: UploadFile,
        text: str | None = None,
        duration_ms: int | None = None,
        reply_mode: ReplyMode | None = None,
        reply_to_message_id: str | None = None,
    ) -> SendMessageResult:
        if self.pings_service is not None:
            await self.pings_service.ensure_can_message(
                sender_id=sender_id,
                receiver_id=receiver_id,
            )

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
            result = await self._create_outgoing_message(
                sender_id=sender_id,
                receiver_id=receiver_id,
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
        return result

    async def send_text_message(
        self,
        *,
        sender_id: str,
        receiver_id: str,
        text: str,
        reply_mode: ReplyMode | None = None,
        reply_to_message_id: str | None = None,
    ) -> SendMessageResult:
        if self.pings_service is not None:
            await self.pings_service.ensure_can_message(
                sender_id=sender_id,
                receiver_id=receiver_id,
            )

        normalized_reply_mode, normalized_reply_to_message_id = (
            self._normalize_reply_fields(
                reply_mode=reply_mode,
                reply_to_message_id=reply_to_message_id,
            )
        )

        return await self._create_outgoing_message(
            sender_id=sender_id,
            receiver_id=receiver_id,
            message_type="text",
            text=self._normalize_text(text),
            reply_mode=normalized_reply_mode,
            reply_to_message_id=normalized_reply_to_message_id,
        )

    async def _create_outgoing_message(
        self,
        *,
        sender_id: str,
        receiver_id: str,
        message_type: str,
        text: str | None = None,
        media: MediaDocument | None = None,
        reply_mode: ReplyMode | None = None,
        reply_to_message_id: str | None = None,
    ) -> SendMessageResult:
        if reply_mode == "quote":
            doc = await self.repo.create_quote_reply(
                sender_id=sender_id,
                receiver_id=receiver_id,
                message_type=message_type,
                text=text,
                media=media,
                reply_to_message_id=reply_to_message_id,
            )
            return SendMessageResult(message=to_message_doc(doc))

        if reply_mode == "thread":
            doc = await self.repo.create_thread_reply(
                sender_id=sender_id,
                receiver_id=receiver_id,
                message_type=message_type,
                text=text,
                media=media,
                reply_to_message_id=reply_to_message_id,
            )
            summary_doc = await self.repo.load_thread_summary(
                message_id=doc.thread_root_id or "",
                user_id=sender_id,
            )
            return SendMessageResult(
                message=to_message_doc(doc),
                thread_summary=to_thread_summary(summary_doc),
            )

        doc = await self.repo.create_message(
            sender_id=sender_id,
            receiver_id=receiver_id,
            message_type=message_type,
            text=text,
            media=media,
        )
        return SendMessageResult(message=to_message_doc(doc))
