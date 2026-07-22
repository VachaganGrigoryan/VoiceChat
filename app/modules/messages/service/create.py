from __future__ import annotations

from datetime import UTC, datetime

from app.core.errors import AppError
from app.db.models import MediaDocument
from app.infra.storage import get_storage
from app.modules.messages.media_policy import resolve_media_policy
from app.modules.messages.repository.mappers import (
    message_text,
    to_message_doc,
    to_thread_summary,
)
from app.modules.messages.schemas import MessageDoc, ReplyMode
from app.modules.messages.service.base import (
    ReleasedScheduledMessage,
    SendMessageResult,
)
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

    async def forward_message_to_conversation(
        self,
        *,
        source_conversation_id: str,
        message_id: str,
        target_conversation_id: str,
        sender_id: str,
    ) -> SendMessageResult:
        source = await self.repo.get_by_id_for_conversation(
            conversation_id=source_conversation_id,
            message_id=message_id,
            user_id=sender_id,
        )
        if source is None:
            raise AppError(
                code="MESSAGE_NOT_FOUND", message="Message not found", status_code=404
            )
        doc = await self.repo.create_forwarded_message(
            source=source,
            target_conversation_id=target_conversation_id,
            sender_id=sender_id,
        )
        await self._materialize_conversation_message(doc)
        summaries = await self.repo.receipt_summaries_for_messages(
            conversation_id=target_conversation_id,
            messages=[doc],
        )
        return SendMessageResult(
            message=to_message_doc(doc, receipt_summary=summaries.get(doc.str_id))
        )

    async def schedule_conversation_message(
        self,
        *,
        conversation_id: str,
        sender_id: str,
        text: str,
        scheduled_for: datetime,
    ) -> MessageDoc:
        if scheduled_for.tzinfo is None:
            scheduled_for = scheduled_for.replace(tzinfo=UTC)
        if scheduled_for <= datetime.now(UTC):
            raise AppError(
                code="INVALID_SCHEDULE_TIME",
                message="scheduled_for must be in the future",
                status_code=400,
            )
        doc = await self.repo.create_scheduled_message(
            conversation_id=conversation_id,
            sender_id=sender_id,
            text=self._normalize_text(text),
            scheduled_for=scheduled_for,
        )
        return to_message_doc(doc)

    async def list_scheduled_messages(
        self, *, conversation_id: str, sender_id: str
    ) -> list[MessageDoc]:
        docs = await self.repo.list_scheduled_for_sender(
            conversation_id=conversation_id, sender_id=sender_id
        )
        return [to_message_doc(doc) for doc in docs]

    async def cancel_scheduled_message(
        self, *, conversation_id: str, message_id: str, sender_id: str
    ) -> None:
        cancelled = await self.repo.cancel_scheduled_message(
            conversation_id=conversation_id,
            message_id=message_id,
            sender_id=sender_id,
        )
        if not cancelled:
            raise AppError(
                code="SCHEDULED_MESSAGE_NOT_FOUND",
                message="Scheduled message not found",
                status_code=404,
            )

    async def release_due_scheduled_messages(
        self, *, now: datetime | None = None, limit: int = 100
    ) -> list[ReleasedScheduledMessage]:
        """Release scheduled messages whose time has arrived.

        Flips each due message to ``sent``, materializes it onto the conversation
        timeline, and returns the released messages together with their conversation
        participant ids so the caller can fan out realtime + notifications.
        """
        released = await self.repo.claim_due_scheduled_messages(
            now=now or datetime.now(UTC), limit=limit
        )
        results: list[ReleasedScheduledMessage] = []
        for doc in released:
            await self._materialize_conversation_message(doc)
            summaries = await self.repo.receipt_summaries_for_messages(
                conversation_id=doc.conversation_id,
                messages=[doc],
            )
            participant_ids: list[str] = []
            if self.conversations_service is not None:
                participant_ids = (
                    await self.conversations_service.conversation_participant_ids(
                        conversation_id=doc.conversation_id
                    )
                )
            results.append(
                ReleasedScheduledMessage(
                    result=SendMessageResult(
                        message=to_message_doc(
                            doc, receipt_summary=summaries.get(doc.str_id)
                        )
                    ),
                    participant_ids=participant_ids,
                )
            )
        return results

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
        mention_user_ids, mention_scope = await self._resolve_mentions(
            conversation_id=conversation_id,
            text=text,
        )

        if reply_mode == "quote":
            doc = await self.repo.create_conversation_quote_reply(
                conversation_id=conversation_id,
                sender_id=sender_id,
                message_type=message_type,
                text=text,
                media=media,
                reply_to_message_id=reply_to_message_id,
                mention_user_ids=mention_user_ids,
                mention_scope=mention_scope,
            )
        elif reply_mode == "thread":
            doc = await self.repo.create_conversation_thread_reply(
                conversation_id=conversation_id,
                sender_id=sender_id,
                message_type=message_type,
                text=text,
                media=media,
                reply_to_message_id=reply_to_message_id,
                mention_user_ids=mention_user_ids,
                mention_scope=mention_scope,
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
                mention_user_ids=mention_user_ids,
                mention_scope=mention_scope,
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
