from __future__ import annotations

from typing import Any
from bson import ObjectId

from app.infra.storage import build_storage_url
from app.modules.messages.schemas import MessageDoc, MediaMeta


def to_message_doc(m: dict[str, Any]) -> MessageDoc:
    def _id(x):
        if isinstance(x, ObjectId):
            return str(x)
        return str(x)

    media = m.get("media")
    if media is not None:
        media_payload = dict(media)
        media_payload["url"] = build_storage_url(media_payload["storage"], media_payload["key"])
        media = MediaMeta(**media_payload)

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
        created_at=m["created_at"],
        updated_at=m["updated_at"],
    )