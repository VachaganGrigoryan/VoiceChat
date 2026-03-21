from __future__ import annotations

from typing import Any

from bson import ObjectId

from app.infra.storage import build_storage_url
from app.modules.messages.schemas import MessageDoc


def to_message_doc(m: dict[str, Any]) -> MessageDoc:
    def _id(x):
        if isinstance(x, ObjectId):
            return str(x)
        return str(x)

    is_deleted = bool(m.get("is_deleted", False))
    deleted_for_everyone = bool(m.get("deleted_for_everyone", False))

    media = m.get("media")
    if media is not None and not is_deleted:
        media = {
            **media,
            "url": build_storage_url(media["storage"], media["key"])
        }

    return MessageDoc(
        id=str(m["_id"]),
        conversation_id=m["conversation_id"],
        sender_id=_id(m["sender_id"]),
        receiver_id=_id(m["receiver_id"]),
        type=m["type"],
        text=None if is_deleted else m.get("text"),
        media=None if is_deleted else media,
        status=m["status"],
        edited_at=m.get("edited_at"),
        delivered_at=m.get("delivered_at"),
        read_at=m.get("read_at"),
        is_deleted=is_deleted,
        deleted_at=m.get("deleted_at"),
        deleted_for_everyone=deleted_for_everyone,
        created_at=m["created_at"],
        updated_at=m["updated_at"],
    )