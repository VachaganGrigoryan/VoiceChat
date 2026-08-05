from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.db.object_id import StrId
from app.modules.messages.schemas import (
    ContentType,
    MediaMeta,
    MentionScope,
    MessageReactionGroup,
    MessageTextStyle,
    MessageType,
    PollRef,
    ReplyMode,
    ReplyPreview,
)


class FeedAuthor(BaseModel):
    id: StrId
    username: str | None = None
    display_name: str | None = None
    avatar: dict | None = None


class FeedPostView(BaseModel):
    """A channel message projected as a feed post.

    Shares the underlying Message model but presents the fields a reader needs to
    render a post rather than a chat bubble (author identity, attachments,
    reaction summary, comment count).
    """

    id: StrId
    channel_id: str
    author: FeedAuthor
    type: MessageType = "text"
    text: str | None = None
    attachments: list[MediaMeta] = Field(default_factory=list)
    reactions: list[MessageReactionGroup] = Field(default_factory=list)
    comment_count: int = 0
    has_thread: bool = False
    is_deleted: bool = False
    style: MessageTextStyle | None = None

    # Projected so a post renders through the same content components a chat
    # message does. Every one is already on the hydrated `MessageDoc` the
    # projection reads, so none of these costs an extra query.
    sender_id: StrId | None = None
    content_type: ContentType | None = None
    reply_mode: ReplyMode | None = None
    reply_to_message_id: str | None = None
    thread_root_id: str | None = None
    reply_preview: ReplyPreview | None = None
    mention_user_ids: list[StrId] = Field(default_factory=list)
    mention_scope: MentionScope | None = None
    poll_ref: PollRef | None = None

    created_at: datetime
    edited_at: datetime | None = None
