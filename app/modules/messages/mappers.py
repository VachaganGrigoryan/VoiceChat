from __future__ import annotations

from typing import Any

from bson import ObjectId

from app.infra.storage import build_storage_url
from app.modules.messages.schemas import (
    CallMeta,
    MessageDoc,
    MessageReactionGroup,
    ReplyPreview,
    ThreadSummary,
)


def _id(x):
    if isinstance(x, ObjectId):
        return str(x)
    return str(x)


def _normalize_message_type(message_type: Any) -> str:
    if message_type in {"text", "media", "file", "call"}:
        return message_type
    return "text"


def _normalize_media(
    *,
    message_type: str,
    media: Any,
) -> dict[str, Any] | None:
    if message_type == "call":
        return None
    if not isinstance(media, dict):
        return None

    normalized_media = dict(media)
    if message_type == "file" and not normalized_media.get("kind"):
        normalized_media["kind"] = "file"

    normalized_media["url"] = build_storage_url(
        normalized_media["storage"],
        normalized_media["key"],
    )
    return normalized_media


def normalize_call_payload(
    *,
    message_type: str,
    call: Any,
) -> CallMeta | None:
    if message_type != "call" or not isinstance(call, dict):
        return None

    return CallMeta(
        call_id=str(call["call_id"]),
        type=call["type"],
        status=call["status"],
        caller_user_id=_id(call["caller_user_id"]),
        callee_user_id=_id(call["callee_user_id"]),
        started_at=call["started_at"],
        answered_at=call.get("answered_at"),
        ended_at=call.get("ended_at"),
        duration_ms=max(int(call.get("duration_ms", 0)), 0),
    )


def normalize_message_record(
    message: dict[str, Any],
) -> tuple[str, dict[str, Any] | None]:
    message_type = _normalize_message_type(message.get("type"))
    media = _normalize_media(
        message_type=message_type,
        media=message.get("media"),
    )
    return message_type, media


def message_is_deleted(m: dict[str, Any]) -> bool:
    hidden_for_user_ids = m.get("hidden_for_user_ids") or []
    return len(hidden_for_user_ids) > 0


def to_message_doc(m: dict[str, Any]) -> MessageDoc:
    is_deleted = message_is_deleted(m)

    normalized_type, media = normalize_message_record(m)
    call = normalize_call_payload(message_type=normalized_type, call=m.get("call"))

    reply_preview = m.get("reply_preview")
    if reply_preview is not None:
        preview_type = _normalize_message_type(reply_preview.get("type"))
        reply_preview = ReplyPreview(
            message_id=str(reply_preview["message_id"]),
            sender_id=_id(reply_preview["sender_id"]),
            type=preview_type,
            media_kind=reply_preview.get("media_kind"),
            text=reply_preview.get("text"),
            is_deleted=bool(reply_preview.get("is_deleted", False)),
        )

    reactions = [
        MessageReactionGroup(
            emoji=reaction["emoji"],
            user_ids=[_id(user_id) for user_id in reaction.get("user_ids", [])],
            count=int(reaction.get("count", len(reaction.get("user_ids", [])))),
            updated_at=reaction["updated_at"],
        )
        for reaction in m.get("reactions", [])
    ]

    return MessageDoc(
        id=str(m["_id"]),
        conversation_id=m["conversation_id"],
        sender_id=_id(m["sender_id"]),
        receiver_id=_id(m["receiver_id"]),
        type=normalized_type,
        text=m.get("text"),
        media=media,
        call=call,
        status=m["status"],
        edited_at=m.get("edited_at"),
        delivered_at=m.get("delivered_at"),
        read_at=m.get("read_at"),
        is_deleted=is_deleted,
        reply_mode=m.get("reply_mode"),
        reply_to_message_id=m.get("reply_to_message_id"),
        thread_root_id=m.get("thread_root_id"),
        reply_preview=reply_preview,
        is_thread_root=bool(m.get("is_thread_root", False)),
        thread_reply_count=int(m.get("thread_reply_count", 0)),
        last_thread_reply_at=m.get("last_thread_reply_at"),
        reactions=reactions,
        created_at=m["created_at"],
        updated_at=m["updated_at"],
    )


def to_thread_summary(m: dict[str, Any]) -> ThreadSummary:
    return ThreadSummary(
        thread_root_id=str(m["_id"]),
        conversation_id=m["conversation_id"],
        is_thread_root=bool(m.get("is_thread_root", False)),
        thread_reply_count=int(m.get("thread_reply_count", 0)),
        last_thread_reply_at=m.get("last_thread_reply_at"),
    )
