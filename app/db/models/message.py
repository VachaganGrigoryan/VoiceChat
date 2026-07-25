from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import Field, model_validator
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

# The two things a message can live in (§29). A conversation is a DM or a group;
# a channel is the feed-shaped container. Nothing else addresses a message.
MessageContainerType = Literal["conversation", "channel"]


class MessageDocument(BaseDocument):
    """A message, addressed by its container rather than by a conversation (§29).

    Threads and channel comments are topology on this one document: a root has
    ``thread_root_id = None`` (a channel root is a Post), a thread reply carries
    ``thread_root_id`` (a channel thread reply is a Comment). There are no
    separate post/comment/thread documents (§31–35).
    """

    container_type: MessageContainerType
    container_id: str = Field(min_length=1)
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
    # Free-form labels used by feed/discovery queries (§29). Normalization and
    # limits land with `dependent-models-cleanup` (§40).
    tags: list[str] = Field(default_factory=list)
    # Scheduled send: withheld from timeline/fan-out until `scheduled_for`.
    scheduled_for: datetime | None = None
    state: Literal["sent", "scheduled"] = "sent"
    reactions: list[MessageReactionDocument] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="before")
    @classmethod
    def _derive_container(cls, data: Any) -> Any:
        """Read a pre-migration row, which carries only `conversation_id`.

        Writes never set `conversation_id` — it is dropped by
        `migrate_message_containers` — but a row the migration has not reached
        yet must still deserialize, so its conversation container is derived
        here. Queries are keyed on `container_*`, so such a row is invisible
        until the backfill runs; this only keeps direct id loads working.
        """
        if not isinstance(data, dict):
            return data
        conversation_id = data.get("conversation_id")
        if not data.get("container_id") and conversation_id:
            data["container_type"] = "conversation"
            data["container_id"] = str(conversation_id)
        return data

    class Settings:
        name = COL_MESSAGES
        indexes = [
            # §98: the container timeline, and the thread/comment pool within it.
            IndexModel(
                [
                    ("container_type", ASCENDING),
                    ("container_id", ASCENDING),
                    ("created_at", DESCENDING),
                ],
                name="ix_messages_container_createdAt_desc",
            ),
            IndexModel(
                [
                    ("container_type", ASCENDING),
                    ("container_id", ASCENDING),
                    ("thread_root_id", ASCENDING),
                    ("created_at", ASCENDING),
                ],
                name="ix_messages_container_threadRoot_createdAt_asc",
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
                [("tags", ASCENDING)],
                name="ix_messages_tags",
            ),
            IndexModel(
                [("content.plaintext.call.call_id", ASCENDING)],
                unique=True,
                partialFilterExpression={"type": "call"},
                name="ux_messages_content_call_call_id",
            ),
            # Cheap due-message lookup for the scheduled-send poller: only indexes
            # documents still awaiting release.
            IndexModel(
                [("state", ASCENDING), ("scheduled_for", ASCENDING)],
                partialFilterExpression={"state": "scheduled"},
                name="ix_messages_state_scheduledFor",
            ),
            # Full-text search over message plaintext (only one text index per
            # collection is permitted).
            IndexModel(
                [("content.plaintext.text", TEXT)],
                name="tx_messages_content_plaintext_text",
            ),
        ]
