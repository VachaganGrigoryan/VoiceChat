from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field
from pymongo import ASCENDING, DESCENDING, TEXT, IndexModel

from app.db.document import BaseDocument
from app.db.collections import COL_MESSAGES
from app.db.models.embedded import (
    ContentType,
    ForwardedFromDocument,
    MessageContentDocument,
    MessageEditDocument,
    MessageReactionDocument,
    ReplyPreviewDocument,
)
from app.db.object_id import StrId


class MessageDocument(BaseDocument):
    conversation_id: str
    sender_id: StrId
    type: ContentType = "text"
    content: MessageContentDocument | None = None
    hidden_for_user_ids: list[StrId] = Field(default_factory=list)
    edited_at: datetime | None = None
    # Append-only prior versions retained on each edit.
    edit_history: list[MessageEditDocument] = Field(default_factory=list)
    reply_mode: Literal["quote", "thread"] | None = None
    reply_to_message_id: str | None = None
    thread_root_id: str | None = None
    reply_preview: ReplyPreviewDocument | None = None
    is_thread_root: bool = False
    thread_reply_count: int = Field(default=0, ge=0)
    last_thread_reply_at: datetime | None = None
    # Mentions resolved at send time for notification targeting.
    mention_user_ids: list[StrId] = Field(default_factory=list)
    mention_scope: Literal["here", "all"] | None = None
    forwarded_from: ForwardedFromDocument | None = None
    # Scheduled send: withheld from timeline/fan-out until `scheduled_for`.
    scheduled_for: datetime | None = None
    state: Literal["sent", "scheduled"] = "sent"
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
                [("sender_id", ASCENDING), ("created_at", DESCENDING)],
                name="ix_messages_sender_createdAt_desc",
            ),
            IndexModel(
                [("content.plaintext.call.call_id", ASCENDING)],
                unique=True,
                partialFilterExpression={"type": "call"},
                name="ux_messages_content_call_call_id",
            ),
            # Full-text search over message plaintext (only one text index per
            # collection is permitted).
            IndexModel(
                [("content.plaintext.text", TEXT)],
                name="tx_messages_content_plaintext_text",
            ),
        ]
