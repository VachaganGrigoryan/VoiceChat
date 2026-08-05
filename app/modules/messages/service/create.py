from __future__ import annotations

from datetime import UTC, datetime

from app.core.errors import AppError
from app.db.models import (
    MediaDocument,
    MessageContainerType,
    PlaintextContentDocument,
    TextStyleDocument,
    PollRefDocument,
)
from app.infra.storage import get_storage
from app.modules.authorization.permissions import (
    MESSAGE_CREATE,
    THREAD_REPLY,
)
from app.modules.messages.media_policy import resolve_media_policy
from app.modules.messages.repository.mappers import (
    message_text,
    to_message_doc,
    to_thread_summary,
)
from app.modules.messages.schemas import (
    MediaAttachmentInput,
    MessageDoc,
    ReplyMode,
    SendRichContentRequest,
)
from app.modules.messages.service.base import (
    ReleasedScheduledMessage,
    SendMessageResult,
)
from fastapi import UploadFile


class CreateMessagesMixin:
    async def upload_media(
        self,
        *,
        container_type: MessageContainerType,
        container_id: str,
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
            return await self._create_message(
                container_type=container_type,
                container_id=container_id,
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

    async def send_text(
        self,
        *,
        container_type: MessageContainerType,
        container_id: str,
        sender_id: str,
        text: str,
        reply_mode: ReplyMode | None = None,
        reply_to_message_id: str | None = None,
        style: TextStyleDocument | None = None,
    ) -> SendMessageResult:
        normalized_reply_mode, normalized_reply_to_message_id = (
            self._normalize_reply_fields(
                reply_mode=reply_mode,
                reply_to_message_id=reply_to_message_id,
            )
        )
        normalized_text = self._normalize_text(text)
        return await self._create_message(
            container_type=container_type,
            container_id=container_id,
            sender_id=sender_id,
            message_type="text",
            text=normalized_text,
            # A style with nothing set is the same as no style at all, so it is
            # dropped rather than persisted as an empty object.
            plaintext=(
                PlaintextContentDocument(text=normalized_text, style=style)
                if style is not None and (style.background or style.align)
                else None
            ),
            reply_mode=normalized_reply_mode,
            reply_to_message_id=normalized_reply_to_message_id,
        )

    async def send_rich_content(
        self,
        *,
        container_type: MessageContainerType,
        container_id: str,
        sender_id: str,
        body: SendRichContentRequest,
    ) -> SendMessageResult:
        text = self._normalize_optional_text(body.text)
        attachments = [
            self._attachment_input_to_document(attachment)
            for attachment in body.attachments
        ]
        plaintext = PlaintextContentDocument(
            text=text,
            media=attachments[0] if body.type == "voice" and attachments else None,
            sticker=(
                body.sticker.model_dump(mode="json")
                if body.sticker is not None
                else None
            ),
            location=(
                body.location.model_dump(mode="json")
                if body.location is not None
                else None
            ),
            contact=(
                body.contact.model_dump(mode="json")
                if body.contact is not None
                else None
            ),
            link_preview=(
                body.link_preview.model_dump(mode="json")
                if body.link_preview is not None
                else None
            ),
        )
        normalized_reply_mode, normalized_reply_to_message_id = (
            self._normalize_reply_fields(
                reply_mode=body.reply_mode,
                reply_to_message_id=body.reply_to_message_id,
            )
        )
        return await self._create_message(
            container_type=container_type,
            container_id=container_id,
            sender_id=sender_id,
            message_type=body.type,
            text=text,
            plaintext=plaintext,
            attachments=attachments,
            reply_mode=normalized_reply_mode,
            reply_to_message_id=normalized_reply_to_message_id,
        )

    async def send_poll_ref_message(
        self,
        *,
        container_type: MessageContainerType,
        container_id: str,
        sender_id: str,
        actor_user_id: str | None = None,
        poll_id: str,
        question: str,
    ) -> SendMessageResult:
        """Post a ``poll``-type message that links to a first-class poll entity.

        The message embeds only a ``poll_ref`` (poll id + denormalized question);
        the poll's options/votes/tallies live in the linked ``PollDocument``.
        ``sender_id`` is the authoring bot, so ``actor_user_id`` names the human
        whose standing in the container is what gets authorized.
        """
        plaintext = PlaintextContentDocument(
            poll_ref=PollRefDocument(poll_id=poll_id, question=question)
        )
        return await self._create_message(
            container_type=container_type,
            container_id=container_id,
            sender_id=sender_id,
            actor_user_id=actor_user_id,
            message_type="poll",
            plaintext=plaintext,
        )

    async def forward_message(
        self,
        *,
        source_container_type: MessageContainerType,
        source_container_id: str,
        message_id: str,
        target_container_type: MessageContainerType,
        target_container_id: str,
        sender_id: str,
    ) -> SendMessageResult:
        source = await self.repo.get_by_id_in_container(
            container_type=source_container_type,
            container_id=source_container_id,
            message_id=message_id,
            user_id=sender_id,
        )
        if source is None:
            raise AppError(
                code="MESSAGE_NOT_FOUND", message="Message not found", status_code=404
            )
        await self._require_container_permission(
            user_id=sender_id,
            action=MESSAGE_CREATE,
            container_type=target_container_type,
            container_id=target_container_id,
            message="Not allowed to post to this container",
        )
        doc = await self.repo.create_forwarded_message(
            source=source,
            target_container_type=target_container_type,
            target_container_id=target_container_id,
            sender_id=sender_id,
        )
        await self._materialize_conversation_message(doc)
        summaries = await self.repo.receipt_summaries_for_messages(
            container_type=target_container_type,
            container_id=target_container_id,
            messages=[doc],
        )
        return SendMessageResult(
            message=to_message_doc(doc, receipt_summary=summaries.get(doc.str_id))
        )

    async def schedule_message(
        self,
        *,
        container_type: MessageContainerType,
        container_id: str,
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
        await self._require_container_permission(
            user_id=sender_id,
            action=MESSAGE_CREATE,
            container_type=container_type,
            container_id=container_id,
            message="Not allowed to post to this container",
        )
        doc = await self.repo.create_scheduled_message(
            container_type=container_type,
            container_id=container_id,
            sender_id=sender_id,
            text=self._normalize_text(text),
            scheduled_for=scheduled_for,
        )
        return to_message_doc(doc)

    async def list_scheduled_messages(
        self,
        *,
        container_type: MessageContainerType,
        container_id: str,
        sender_id: str,
    ) -> list[MessageDoc]:
        docs = await self.repo.list_scheduled_for_sender(
            container_type=container_type,
            container_id=container_id,
            sender_id=sender_id,
        )
        return [to_message_doc(doc) for doc in docs]

    async def cancel_scheduled_message(
        self,
        *,
        container_type: MessageContainerType,
        container_id: str,
        message_id: str,
        sender_id: str,
    ) -> None:
        cancelled = await self.repo.cancel_scheduled_message(
            container_type=container_type,
            container_id=container_id,
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
        timeline, and returns the released messages together with their container
        participant ids so the caller can fan out realtime + notifications.
        """
        released = await self.repo.claim_due_scheduled_messages(
            now=now or datetime.now(UTC), limit=limit
        )
        results: list[ReleasedScheduledMessage] = []
        for doc in released:
            await self._materialize_conversation_message(doc)
            summaries = await self.repo.receipt_summaries_for_messages(
                container_type=doc.container_type,
                container_id=doc.container_id,
                messages=[doc],
            )
            participant_ids: list[str] = []
            if (
                doc.container_type == "conversation"
                and self.conversations_service is not None
            ):
                participant_ids = (
                    await self.conversations_service.conversation_participant_ids(
                        conversation_id=doc.container_id
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

    async def _create_message(
        self,
        *,
        container_type: MessageContainerType,
        container_id: str,
        sender_id: str,
        message_type: str,
        actor_user_id: str | None = None,
        text: str | None = None,
        media: MediaDocument | None = None,
        plaintext: PlaintextContentDocument | None = None,
        attachments: list[MediaDocument] | None = None,
        reply_mode: ReplyMode | None = None,
        reply_to_message_id: str | None = None,
    ) -> SendMessageResult:
        # Posting and replying are distinct rights: a channel may accept
        # comments from an audience it does not let post (§59). The actor is who
        # gets authorized — for bot-authored content that is not the sender.
        await self._require_container_permission(
            user_id=actor_user_id or sender_id,
            action=THREAD_REPLY if reply_mode == "thread" else MESSAGE_CREATE,
            container_type=container_type,
            container_id=container_id,
            message="Not allowed to post to this container",
        )

        thread_summary = None
        mention_user_ids, mention_scope = await self._resolve_mentions(
            container_type=container_type,
            container_id=container_id,
            text=text,
        )

        if reply_mode == "quote":
            doc = await self.repo.create_quote_reply(
                container_type=container_type,
                container_id=container_id,
                sender_id=sender_id,
                message_type=message_type,
                text=text,
                media=media,
                plaintext=plaintext,
                attachments=attachments,
                reply_to_message_id=reply_to_message_id,
                mention_user_ids=mention_user_ids,
                mention_scope=mention_scope,
            )
        elif reply_mode == "thread":
            doc = await self.repo.create_thread_reply(
                container_type=container_type,
                container_id=container_id,
                sender_id=sender_id,
                message_type=message_type,
                text=text,
                media=media,
                plaintext=plaintext,
                attachments=attachments,
                reply_to_message_id=reply_to_message_id,
                mention_user_ids=mention_user_ids,
                mention_scope=mention_scope,
            )
            summary_doc = await self.repo.load_thread_summary(
                container_type=container_type,
                container_id=container_id,
                message_id=doc.thread_root_id or "",
                user_id=sender_id,
            )
            thread_summary = to_thread_summary(summary_doc)
        else:
            doc = await self.repo.create_message(
                container_type=container_type,
                container_id=container_id,
                sender_id=sender_id,
                message_type=message_type,
                text=text,
                media=media,
                plaintext=plaintext,
                attachments=attachments,
                mention_user_ids=mention_user_ids,
                mention_scope=mention_scope,
            )

        if doc.container_type == "channel" and self.channels_repo is not None:
            await self.channels_repo.record_message(
                channel_id=doc.container_id,
                message_id=doc.str_id,
                created_at=doc.created_at,
            )
        if reply_mode != "thread":
            await self._materialize_conversation_message(doc)
        summaries = await self.repo.receipt_summaries_for_messages(
            container_type=container_type,
            container_id=container_id,
            messages=[doc],
        )
        return SendMessageResult(
            message=to_message_doc(doc, receipt_summary=summaries.get(doc.str_id)),
            thread_summary=thread_summary,
        )

    async def _materialize_conversation_message(self, doc) -> None:
        """Advance the conversation's inbox preview for a just-created message.

        Only conversations carry an inbox row; a channel's activity is tracked on
        the channel entity itself (`channels-and-profile-feed`).
        """
        if self.conversations_service is None or doc.container_type != "conversation":
            return
        await self.conversations_service.materialize_conversation_message(
            conversation_id=doc.container_id,
            sender_id=str(doc.sender_id),
            message_id=doc.str_id,
            message_type=doc.type,
            preview_text=message_text(doc),
            created_at=doc.created_at,
        )

    def _attachment_input_to_document(
        self, attachment: MediaAttachmentInput
    ) -> MediaDocument:
        return MediaDocument(
            kind=attachment.kind,
            storage=attachment.storage,
            key=attachment.key,
            mime=attachment.mime,
            size_bytes=attachment.size_bytes,
            duration_ms=attachment.duration_ms,
        )
