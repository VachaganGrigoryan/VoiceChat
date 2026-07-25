from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.db.object_id import StrId
from app.modules.messages.schemas import MediaMeta, MessageReactionGroup, MessageType


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
    created_at: datetime
    edited_at: datetime | None = None
