from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.modules.messages.schemas import MessageTextStyle, ReplyMode

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


class ViewerBlock(BaseModel):
    """A cold deep link's first paint, in the same response as the resource.

    Deliberately minimal: the full contract is `/viewer/capabilities`. This
    exists so a client that has just fetched a channel can decide whether to
    render an enabled composer without a second round trip. It is derived from
    the same decision function, so it can never disagree with the full result.
    """

    can_post: bool = False
    can_comment: bool = False
    can_manage: bool = False
    membership_status: str | None = None
    is_follower: bool = False


class ChannelSummary(BaseModel):
    """The canonical shape for a channel appearing in a list.

    One shape for every list context — the user's channels, a space's channels,
    the channel inbox, the directory — so a client parses a channel the same way
    everywhere. Readability is `visibility` and nothing else; there is no second
    vocabulary for the same question.
    """

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
    tags: list[str]
    message_count: int
    follower_count: int
    last_activity_at: datetime | None
    is_main: bool = False
    #: Absent on projections that do not resolve it; a client treats `None` as
    #: "not yet known" and asks `/viewer/capabilities`.
    viewer: ViewerBlock | None = None


class ChannelView(ChannelSummary):
    """The summary plus the policy and audit fields a detail view needs."""

    join_policy: ChannelJoinPolicy
    posting_policy: ChannelPostingPolicy
    comment_policy: ChannelCommentPolicy
    last_message_id: str | None
    pinned_message_ids: list[str] = Field(default_factory=list)
    legacy_conversation_id: str | None
    created_by: str
    created_at: datetime
    updated_at: datetime


class ChannelMessageCreateRequest(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    reply_mode: ReplyMode | None = None
    reply_to_message_id: str | None = None
    style: MessageTextStyle | None = None


class ChannelViewerStateView(BaseModel):
    """The caller's own state on a channel, mirroring the conversation inbox row."""

    channel_id: str
    pinned: bool = False
    archived: bool = False
    folder: str | None = None
    muted_until: datetime | None = None
    notification_level: Literal["all", "mentions", "none"] = "all"
    last_read_message_id: str | None = None


class ChannelInboxRow(BaseModel):
    channel: ChannelView
    state: ChannelViewerStateView
    unread_count: int = 0
    joined: bool = False


class ChannelInboxUpdateRequest(BaseModel):
    pinned: bool | None = None
    archived: bool | None = None
    folder: str | None = None


class ChannelNotificationUpdateRequest(BaseModel):
    notification_level: Literal["all", "mentions", "none"] | None = None
    muted_until: datetime | None = None


class ChannelMemberView(BaseModel):
    relationship_id: str
    user_id: str
    status: str
    role_ids: list[str] = []
    requested_at: datetime | None = None
    activated_at: datetime | None = None
