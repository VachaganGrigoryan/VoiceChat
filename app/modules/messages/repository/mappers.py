from __future__ import annotations

from typing import Any

from app.db.models import (
    CallMessageDocument,
    MediaDocument,
    MessageDocument,
    PlaintextContentDocument,
)
from app.infra.storage import build_storage_url
from app.modules.messages.schemas import (
    CallMeta,
    MediaMeta,
    MessageContent,
    MessageDoc,
    MessagePlaintext,
    MessageReceiptSummary,
    MessageReactionGroup,
    ReplyPreview,
    ThreadSummary,
)


def _normalize_message_type(message_type: Any) -> str:
    if message_type in {"text", "media", "file", "call"}:
        return message_type
    return "text"


def _normalize_media(
    *,
    message_type: str,
    media,
) -> MediaMeta | None:
    if message_type == "call":
        return None
    if media is None:
        return None

    return MediaMeta(
        kind=media.kind,
        storage=media.storage,
        key=media.key,
        url=build_storage_url(media.storage, media.key),
        mime=media.mime,
        size_bytes=media.size_bytes,
        duration_ms=media.duration_ms,
    )


def normalize_call_payload(
    *,
    message_type: str,
    call: CallMessageDocument | None,
) -> CallMeta | None:
    if message_type != "call" or call is None:
        return None

    return CallMeta(
        call_id=call.call_id,
        type=call.type,
        status=call.status,
        caller_user_id=str(call.caller_user_id),
        callee_user_id=str(call.callee_user_id),
        started_at=call.started_at,
        answered_at=call.answered_at,
        ended_at=call.ended_at,
        duration_ms=max(int(call.duration_ms), 0),
    )


def _stored_plaintext(message: MessageDocument) -> PlaintextContentDocument | None:
    content = message.content
    if content is None or content.encryption != "none":
        return None
    return content.plaintext


def message_text(message: MessageDocument) -> str | None:
    plaintext = _stored_plaintext(message)
    return plaintext.text if plaintext is not None else None


def message_media(message: MessageDocument) -> MediaDocument | None:
    plaintext = _stored_plaintext(message)
    return plaintext.media if plaintext is not None else None


def message_call(message: MessageDocument) -> CallMessageDocument | None:
    plaintext = _stored_plaintext(message)
    return plaintext.call if plaintext is not None else None


def normalize_message_record(message: MessageDocument) -> tuple[str, MediaMeta | None]:
    message_type = _normalize_message_type(message.type)
    media = _normalize_media(message_type=message_type, media=message_media(message))
    return message_type, media


def _build_content(
    *,
    message: MessageDocument,
    normalized_type: str,
    media: MediaMeta | None,
    call: CallMeta | None,
) -> MessageContent:
    """Build the wire content envelope.

    Prefers the stored `content` envelope (E2EE-ready); falls back to synthesizing
    a plaintext envelope from the legacy flat fields for messages written before
    the envelope existed.
    """
    stored = message.content
    if stored is not None and getattr(stored, "encryption", "none") == "e2ee":
        return MessageContent(
            encryption="e2ee",
            type=getattr(stored, "type", normalized_type),
            ciphertext=getattr(stored, "ciphertext", None),
            envelope=(
                stored.envelope.model_dump(mode="json")
                if getattr(stored, "envelope", None) is not None
                else None
            ),
        )

    return MessageContent(
        encryption="none",
        type=normalized_type,
        plaintext=MessagePlaintext(text=message_text(message), media=media, call=call),
    )


def to_message_doc(
    message: MessageDocument,
    *,
    receipt_summary: MessageReceiptSummary | None = None,
) -> MessageDoc:
    normalized_type, media = normalize_message_record(message)
    call = normalize_call_payload(
        message_type=normalized_type, call=message_call(message)
    )

    reply_preview = message.reply_preview
    if reply_preview is not None:
        preview_type = _normalize_message_type(reply_preview.type)
        reply_preview = ReplyPreview(
            message_id=reply_preview.message_id,
            sender_id=str(reply_preview.sender_id),
            type=preview_type,
            media_kind=reply_preview.media_kind,
            text=reply_preview.text,
            is_deleted=reply_preview.is_deleted,
        )

    reactions = [
        MessageReactionGroup(
            emoji=reaction.emoji,
            user_ids=[str(user_id) for user_id in reaction.user_ids],
            count=int(reaction.count),
            updated_at=reaction.updated_at,
        )
        for reaction in message.reactions
    ]

    content = _build_content(
        message=message,
        normalized_type=normalized_type,
        media=media,
        call=call,
    )

    return MessageDoc(
        id=message.str_id,
        conversation_id=message.conversation_id,
        sender_id=str(message.sender_id),
        type=normalized_type,
        content=content,
        receipt_summary=receipt_summary or MessageReceiptSummary(),
        edited_at=message.edited_at,
        # Live documents are never deleted in-place; a hard delete removes the
        # document entirely and the tombstone lives on referencing reply previews.
        is_deleted=False,
        reply_mode=message.reply_mode,
        reply_to_message_id=message.reply_to_message_id,
        thread_root_id=message.thread_root_id,
        reply_preview=reply_preview,
        is_thread_root=message.is_thread_root,
        thread_reply_count=int(message.thread_reply_count),
        last_thread_reply_at=message.last_thread_reply_at,
        reactions=reactions,
        created_at=message.created_at,
        updated_at=message.updated_at,
    )


def to_thread_summary(message: MessageDocument) -> ThreadSummary:
    return ThreadSummary(
        thread_root_id=message.str_id,
        conversation_id=message.conversation_id,
        is_thread_root=message.is_thread_root,
        thread_reply_count=int(message.thread_reply_count),
        last_thread_reply_at=message.last_thread_reply_at,
    )
