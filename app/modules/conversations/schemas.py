from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, model_validator

from app.db.object_id import StrId
from app.modules.pings.schemas import PingStatusView

ReplyMode = Literal["quote", "thread"]

ConversationType = Literal["dm", "group", "channel", "thread"]
EncryptionMode = Literal["none", "e2ee"]
ParticipantRole = Literal["owner", "admin", "member", "subscriber"]
PreviewType = Literal["text", "media", "file", "call", "system"]
ConversationVisibility = Literal["private", "public"]
PostingPolicy = Literal["everyone", "admins"]
NotificationLevel = Literal["all", "mentions", "none"]


class ConversationPreview(BaseModel):
    message_id: StrId
    sender_id: StrId
    type: PreviewType
    text: str | None = None
    created_at: datetime


class ConversationUserSummary(BaseModel):
    id: StrId
    username: str | None = None
    display_name: str | None = None
    avatar: dict | None = None
    is_online: bool = False
    can_ping: bool | None = None
    chat_allowed: bool | None = None
    ping_status: PingStatusView | None = None
    is_ghost: bool = False


class ConversationView(BaseModel):
    id: StrId
    type: ConversationType
    encryption: EncryptionMode
    participant_ids: list[StrId]
    created_by: StrId
    title: str | None = None
    image: dict | None = None
    visibility: ConversationVisibility = "private"
    posting_policy: PostingPolicy = "everyone"
    space_id: StrId | None = None
    parent_conversation_id: StrId | None = None
    root_message_id: str | None = None
    slug: str | None = None
    description: str | None = None
    member_count: int = Field(default=0, ge=0)
    pinned_message_ids: list[str] = Field(default_factory=list)
    settings: dict[str, Any] = Field(default_factory=dict)
    peer_user: ConversationUserSummary | None = None
    participant_users: list[ConversationUserSummary] = Field(default_factory=list)
    last_message_at: datetime | None = None
    last_message_preview: ConversationPreview | None = None
    unread_count: int = 0
    created_at: datetime
    updated_at: datetime


class ParticipantView(BaseModel):
    conversation_id: StrId
    user_id: StrId
    role: ParticipantRole
    permissions: dict[str, bool] | None = None
    joined_at: datetime
    last_read_at: datetime | None = None
    last_read_message_id: str | None = None
    notification_level: NotificationLevel = "all"
    muted_until: datetime | None = None
    archived: bool = False
    pinned: bool = False
    folder: str | None = None
    invited_by: StrId | None = None
    draft_updated_at: datetime | None = None
    muted: bool = False
    hidden: bool = False


class CreateDmRequest(BaseModel):
    peer_user_id: str


class CreateGroupRequest(BaseModel):
    title: str = Field(min_length=1, max_length=80)
    participant_ids: list[str] = Field(min_length=1, max_length=100)


class UpdateGroupRequest(BaseModel):
    title: str = Field(min_length=1, max_length=80)


class AddGroupMembersRequest(BaseModel):
    participant_ids: list[str] = Field(min_length=1, max_length=100)


class UpdateParticipantRoleRequest(BaseModel):
    role: Literal["admin", "member"]


class TransferOwnershipRequest(BaseModel):
    user_id: str


class ConversationSendTextRequest(BaseModel):
    """Conversation-scoped text send; the receiver is derived from the path."""

    text: str = Field(min_length=1, max_length=4000)
    reply_mode: Optional[ReplyMode] = None
    reply_to_message_id: Optional[str] = None

    @model_validator(mode="after")
    def validate_reply_fields(self) -> "ConversationSendTextRequest":
        if self.reply_mode and not self.reply_to_message_id:
            raise ValueError("reply_to_message_id is required when reply_mode is set")
        if self.reply_to_message_id and not self.reply_mode:
            raise ValueError("reply_mode is required when reply_to_message_id is set")
        return self
