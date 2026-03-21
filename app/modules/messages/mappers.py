from __future__ import annotations

from typing import Any

from bson import ObjectId

from app.infra.storage import build_storage_url
from app.modules.messages.schemas import MessageDoc, ReplyPreview, MessageReactionGroup, ThreadSummary


def _id(x):
    if isinstance(x, ObjectId):
        return str(x)
    return str(x)


def message_is_deleted(m: dict[str, Any]) -> bool:
    hidden_for_user_ids = m.get("hidden_for_user_ids") or []
    return len(hidden_for_user_ids) > 0


def to_message_doc(m: dict[str, Any]) -> MessageDoc:
    is_deleted = message_is_deleted(m)

    media = m.get("media")
    if media is not None:
        media = {
            **media,
            "url": build_storage_url(media["storage"], media["key"])
        }

    reply_preview = m.get("reply_preview")
    if reply_preview is not None:
        reply_preview = ReplyPreview(
            message_id=str(reply_preview["message_id"]),
            sender_id=_id(reply_preview["sender_id"]),
            type=reply_preview.get("type", "text"),
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
        type=m["type"],
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
