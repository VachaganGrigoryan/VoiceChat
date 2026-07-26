from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.modules.messages.schemas import ReplyMode

ChannelKind = Literal["profile", "text", "announcement"]
ChannelVisibility = Literal["public", "members", "private"]
ChannelJoinPolicy = Literal["open", "approval", "invite_only", "closed"]
ChannelPostingPolicy = Literal["owner", "moderators", "members", "everyone"]
ChannelCommentPolicy = Literal["disabled", "followers", "members", "everyone"]


class ChannelCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    slug: str = Field(
        min_length=1,
        max_length=80,
        pattern=r"^[a-z0-9](?:[a-z0-9-]{0,78}[a-z0-9])?$",
    )
    kind: Literal["text", "announcement"] = "text"
    description: str | None = Field(default=None, max_length=500)
    visibility: ChannelVisibility = "public"
    join_policy: ChannelJoinPolicy = "open"
    posting_policy: ChannelPostingPolicy = "everyone"
    comment_policy: ChannelCommentPolicy = "everyone"
    tags: list[str] = Field(default_factory=list, max_length=20)


class ChannelUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    description: str | None = Field(default=None, max_length=500)
    avatar: dict[str, Any] | None = None
    banner: dict[str, Any] | None = None
    visibility: ChannelVisibility | None = None
    join_policy: ChannelJoinPolicy | None = None
    posting_policy: ChannelPostingPolicy | None = None
    comment_policy: ChannelCommentPolicy | None = None
    tags: list[str] | None = Field(default=None, max_length=20)


class ChannelOwnerView(BaseModel):
    type: Literal["user", "space"]
    id: str


class ChannelView(BaseModel):
    id: str
    owner: ChannelOwnerView
    space_id: str | None
    kind: ChannelKind
    slug: str
    name: str
    description: str | None
    avatar: dict[str, Any] | None
    banner: dict[str, Any] | None
    visibility: ChannelVisibility
    join_policy: ChannelJoinPolicy
    posting_policy: ChannelPostingPolicy
    comment_policy: ChannelCommentPolicy
    tags: list[str]
    message_count: int
    follower_count: int
    last_message_id: str | None
    last_activity_at: datetime | None
    legacy_conversation_id: str | None
    created_by: str
    created_at: datetime
    updated_at: datetime


class ChannelMessageCreateRequest(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    reply_mode: ReplyMode | None = None
    reply_to_message_id: str | None = None
