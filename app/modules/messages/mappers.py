from __future__ import annotations

from typing import Any
from bson import ObjectId

from app.infra.storage import build_audio_url
from app.modules.messages.schemas import MessageDoc, AudioMeta


def to_message_doc(m: dict[str, Any]) -> MessageDoc:
    def _id(x):
        if isinstance(x, ObjectId):
            return str(x)
        return str(x)

    audio = dict(m["audio"])
    audio["url"] = build_audio_url(audio["storage"], audio["key"])

    return MessageDoc(
        id=str(m["_id"]),
        conversation_id=m["conversation_id"],
        sender_id=_id(m["sender_id"]),
        receiver_id=_id(m["receiver_id"]),
        type=m["type"],
        audio=AudioMeta(**audio),
        status=m["status"],
        delivered_at=m.get("delivered_at"),
        read_at=m.get("read_at"),
        created_at=m["created_at"],
        updated_at=m["updated_at"],
    )