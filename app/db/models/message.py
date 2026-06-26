from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field
from pymongo import ASCENDING, DESCENDING, IndexModel

from app.db.document import BaseDocument
from app.db.collections import COL_MESSAGES
from app.db.models.embedded import (
    CallMessageDocument,
    MediaDocument,
    MessageReactionDocument,
    ReplyPreviewDocument,
)
from app.db.object_id import StrId


class MessageDocument(BaseDocument):
    conversation_id: str
    sender_id: StrId
    receiver_id: StrId
    type: Literal["text", "media", "file", "call"] = "text"
    text: str | None = None
    media: MediaDocument | None = None
    call: CallMessageDocument | None = None
    hidden_for_user_ids: list[StrId] = Field(default_factory=list)
    status: Literal["sent", "delivered", "read"] = "sent"
    edited_at: datetime | None = None
    delivered_at: datetime | None = None
    read_at: datetime | None = None
    reply_mode: Literal["quote", "thread"] | None = None
    reply_to_message_id: str | None = None
    thread_root_id: str | None = None
    reply_preview: ReplyPreviewDocument | None = None
    is_thread_root: bool = False
    thread_reply_count: int = Field(default=0, ge=0)
    last_thread_reply_at: datetime | None = None
    reactions: list[MessageReactionDocument] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime

    class Settings:
        name = COL_MESSAGES
        indexes = [
            IndexModel(
                [("conversation_id", ASCENDING), ("created_at", DESCENDING)],
                name="ix_messages_conversation_createdAt_desc",
            ),
            IndexModel(
                [
                    ("conversation_id", ASCENDING),
                    ("thread_root_id", ASCENDING),
                    ("created_at", DESCENDING),
                ],
                name="ix_messages_conversation_threadRoot_createdAt_desc",
            ),
            IndexModel(
                [("thread_root_id", ASCENDING), ("created_at", ASCENDING)],
                name="ix_messages_threadRoot_createdAt_asc",
            ),
            IndexModel(
                [("reply_to_message_id", ASCENDING)],
                name="ix_messages_replyToMessageId",
            ),
            IndexModel(
                [("receiver_id", ASCENDING), ("created_at", DESCENDING)],
                name="ix_messages_receiver_createdAt_desc",
            ),
            IndexModel(
                [("sender_id", ASCENDING), ("created_at", DESCENDING)],
                name="ix_messages_sender_createdAt_desc",
            ),
            IndexModel(
                [
                    ("conversation_id", ASCENDING),
                    ("receiver_id", ASCENDING),
                    ("status", ASCENDING),
                    ("created_at", DESCENDING),
                ],
                name="ix_messages_conversation_receiver_status_createdAt_desc",
            ),
            IndexModel(
                [("call.call_id", ASCENDING)],
                unique=True,
                partialFilterExpression={"type": "call"},
                name="ux_messages_call_call_id",
            ),
        ]
