from __future__ import annotations

from typing import Any

from bson import ObjectId

from app.infra.storage import build_storage_url
from app.modules.messages.schemas import (
    MessageDoc,
    MessageReactionGroup,
    ReplyPreview,
    ThreadSummary,
)

LEGACY_MEDIA_KIND_BY_TYPE = {
    "voice": "voice",
    "image": "image",
    "video": "video",
    "sticker": "image",
}


def _id(x):
    if isinstance(x, ObjectId):
        return str(x)
    return str(x)


def _legacy_media_kind(message_type: Any) -> str | None:
    if not isinstance(message_type, str):
        return None
    return LEGACY_MEDIA_KIND_BY_TYPE.get(message_type)


def _normalize_preview_type(
    *,
    message_type: Any,
    media_kind: Any = None,
) -> tuple[str, str | None]:
    legacy_kind = _legacy_media_kind(message_type)
    if legacy_kind is not None:
        return "media", legacy_kind

    if message_type == "emoji":
        return "text", None

    if message_type == "media":
        return "media", media_kind if isinstance(media_kind, str) else None

    if message_type == "file":
        return "file", "file"

    if message_type == "text":
        return "text", None

    if isinstance(media_kind, str) and media_kind == "file":
        return "file", "file"

    return "text", None


def _infer_media_kind_from_mime(mime: Any) -> str:
    if not isinstance(mime, str):
        return "file"

    normalized = mime.lower().strip()
    if normalized.startswith("audio/"):
        return "audio"
    if normalized.startswith("image/"):
        return "image"
    if normalized.startswith("video/"):
        return "video"
    return "file"


def normalize_message_record(
    message: dict[str, Any],
) -> tuple[str, dict[str, Any] | None]:
    media = message.get("media")
    normalized_media = dict(media) if isinstance(media, dict) else None

    message_type = message.get("type")
    normalized_type, normalized_kind = _normalize_preview_type(
        message_type=message_type,
        media_kind=normalized_media.get("kind") if normalized_media else None,
    )

    if normalized_media is not None:
        if normalized_type == "media":
            normalized_kind = normalized_kind or _infer_media_kind_from_mime(
                normalized_media.get("mime")
            )
        elif normalized_type == "file":
            normalized_kind = "file"

        normalized_media["kind"] = normalized_kind or "file"
        normalized_media["url"] = build_storage_url(
            normalized_media["storage"],
            normalized_media["key"],
        )

    return normalized_type, normalized_media


def message_is_deleted(m: dict[str, Any]) -> bool:
    hidden_for_user_ids = m.get("hidden_for_user_ids") or []
    return len(hidden_for_user_ids) > 0


def to_message_doc(m: dict[str, Any]) -> MessageDoc:
    is_deleted = message_is_deleted(m)

    normalized_type, media = normalize_message_record(m)

    reply_preview = m.get("reply_preview")
    if reply_preview is not None:
        preview_type, preview_media_kind = _normalize_preview_type(
            message_type=reply_preview.get("type", "text"),
            media_kind=reply_preview.get("media_kind"),
        )
        reply_preview = ReplyPreview(
            message_id=str(reply_preview["message_id"]),
            sender_id=_id(reply_preview["sender_id"]),
            type=preview_type,
            media_kind=preview_media_kind,
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
