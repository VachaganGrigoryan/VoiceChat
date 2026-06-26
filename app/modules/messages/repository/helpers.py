from __future__ import annotations

from dataclasses import dataclass

from app.db.models import CallDocument, MessageDocument, ReplyPreviewDocument
from app.modules.messages.repository.mappers import normalize_message_record

REPLY_PREVIEW_MAX_TEXT = 160


@dataclass(slots=True)
class ConversationListRow:
    conversation_id: str
    last_message: MessageDocument
    unread_count: int


def truncate_preview_text(value: str | None) -> str | None:
    if value is None:
        return None

    normalized = value.strip()
    if not normalized:
        return None
    if len(normalized) <= REPLY_PREVIEW_MAX_TEXT:
        return normalized
    return normalized[: REPLY_PREVIEW_MAX_TEXT - 3].rstrip() + "..."


def call_duration_ms(call_doc: CallDocument) -> int:
    answered_at = call_doc.answered_at
    ended_at = call_doc.ended_at
    if answered_at is None or ended_at is None:
        return 0

    return max(int((ended_at - answered_at).total_seconds() * 1000), 0)


def build_reply_preview(message: MessageDocument) -> ReplyPreviewDocument:
    # The target is a live document, so it is never deleted here. The is_deleted
    # tombstone is only stamped later, by the hard-delete cascade.
    message_type, media = normalize_message_record(message)
    return ReplyPreviewDocument(
        message_id=message.str_id,
        sender_id=str(message.sender_id),
        type=message_type,
        media_kind=media.kind if media is not None else None,
        text=truncate_preview_text(message.text),
        is_deleted=False,
    )


def message_participants(message: MessageDocument) -> set[str]:
    return {
        str(message.sender_id),
        str(message.receiver_id),
    }


def conversation_id_for(user_a: str, user_b: str) -> str:
    """
    Stable conversation id: sort by string form of ObjectId.
    """
    a = str(user_a)
    b = str(user_b)
    return f"{a}_{b}" if a < b else f"{b}_{a}"
