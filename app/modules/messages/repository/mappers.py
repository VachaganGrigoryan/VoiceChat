from __future__ import annotations

from typing import Any

from app.db.models import (
    CallMessageDocument,
    MediaDocument,
    MessageContentDocument,
    MessageDocument,
    PlaintextContentDocument,
    PollRefDocument,
)
from app.infra.storage import build_storage_url
from app.modules.messages.schemas import (
    CallMeta,
    ForwardedFrom,
    MediaMeta,
    MessageContent,
    MessageDoc,
    MessageEdit,
    MessagePlaintext,
    MessageReceiptSummary,
    MessageReactionGroup,
    PollRef,
    ReplyPreview,
    ThreadSummary,
)


def _normalize_message_type(message_type: Any) -> str:
    if message_type in {
        "text",
        "media",
        "file",
        "call",
        "system",
        "poll",
        "sticker",
        "voice",
        "location",
        "contact",
        "link_preview",
    }:
        return message_type
    return "text"


def _media_meta_from_document(media: MediaDocument | None) -> MediaMeta | None:
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


def _normalize_media(
    *,
    message_type: str,
    media,
) -> MediaMeta | None:
    if message_type == "call":
        return None
    if media is None:
        return None

    return _media_meta_from_document(media)


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
    if plaintext is None:
        return None
    if plaintext.text:
        return plaintext.text
    if plaintext.poll_ref:
        return plaintext.poll_ref.question or "Poll"
    if plaintext.poll:
        return str(plaintext.poll.get("question") or "Poll")
    if plaintext.location:
        return str(plaintext.location.get("name") or "Location")
    if plaintext.contact:
        return str(plaintext.contact.get("display_name") or "Contact")
    if plaintext.link_preview:
        return str(
            plaintext.link_preview.get("title")
            or plaintext.link_preview.get("url")
            or "Link"
        )
    if plaintext.sticker:
        return str(
            plaintext.sticker.get("label")
            or plaintext.sticker.get("emoji")
            or "Sticker"
        )
    return None


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


def _content_attachments(content: MessageContentDocument | None) -> list[MediaMeta]:
    if content is None:
        return []
    return [
        attachment
        for attachment in (
            _media_meta_from_document(media) for media in content.attachments
        )
        if attachment is not None
    ]


def _poll_ref_view(poll_ref: PollRefDocument | None) -> PollRef | None:
    if poll_ref is None:
        return None
    return PollRef(poll_id=str(poll_ref.poll_id), question=poll_ref.question)


def _stored_content_view(content: MessageContentDocument) -> MessageContent:
    message_type = _normalize_message_type(content.type)
    if content.encryption == "e2ee":
        return MessageContent(
            encryption="e2ee",
            type=message_type,
            attachments=_content_attachments(content),
            ciphertext=content.ciphertext,
            envelope=(
                content.envelope.model_dump(mode="json")
                if content.envelope is not None
                else None
            ),
        )

    plaintext = content.plaintext
    media = _media_meta_from_document(
        plaintext.media if plaintext is not None else None
    )
    call = normalize_call_payload(
        message_type=message_type,
        call=plaintext.call if plaintext is not None else None,
    )
    return MessageContent(
        encryption="none",
        type=message_type,
        plaintext=MessagePlaintext(
            text=plaintext.text if plaintext is not None else None,
            media=media,
            call=call,
            poll=plaintext.poll if plaintext is not None else None,
            poll_ref=_poll_ref_view(
                plaintext.poll_ref if plaintext is not None else None
            ),
            sticker=plaintext.sticker if plaintext is not None else None,
            location=plaintext.location if plaintext is not None else None,
            contact=plaintext.contact if plaintext is not None else None,
            link_preview=plaintext.link_preview if plaintext is not None else None,
        ),
        attachments=_content_attachments(content),
    )


def _build_content(
    *,
    message: MessageDocument,
    normalized_type: str,
    media: MediaMeta | None,
    call: CallMeta | None,
) -> MessageContent:
    """Build the wire content envelope."""
    stored = message.content
    if stored is not None:
        return _stored_content_view(stored)

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
    edit_history = [
        MessageEdit(
            content=_stored_content_view(edit.content), edited_at=edit.edited_at
        )
        for edit in message.edit_history
    ]
    forwarded_from = (
        ForwardedFrom(
            conversation_id=message.forwarded_from.conversation_id,
            message_id=message.forwarded_from.message_id,
            sender_id=str(message.forwarded_from.sender_id),
            forwarded_at=message.forwarded_from.forwarded_at,
        )
        if message.forwarded_from is not None
        else None
    )

    return MessageDoc(
        id=message.str_id,
        conversation_id=message.conversation_id,
        sender_id=str(message.sender_id),
        type=normalized_type,
        content=content,
        receipt_summary=receipt_summary or MessageReceiptSummary(),
        edited_at=message.edited_at,
        edit_history=edit_history,
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
        mention_user_ids=[str(user_id) for user_id in message.mention_user_ids],
        mention_scope=message.mention_scope,
        forwarded_from=forwarded_from,
        scheduled_for=message.scheduled_for,
        state=message.state,
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
