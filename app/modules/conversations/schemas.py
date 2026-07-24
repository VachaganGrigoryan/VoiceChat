from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, model_validator

from app.db.object_id import StrId
from app.modules.messages.schemas import MessageDoc
from app.modules.pings.schemas import PingStatusView
from app.modules.realtime.presence.base import PresenceState

ReplyMode = Literal["quote", "thread"]

ConversationType = Literal["dm", "group", "channel", "thread"]
EncryptionMode = Literal["none", "e2ee"]
ParticipantRole = Literal["owner", "admin", "member", "subscriber"]
PreviewType = Literal[
    "text",
    "media",
    "file",
    "call",
    "system",
    "poll",
    "sticker",
    "voice",
    "location",
    "contact",
    "link_preview",
]
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
    presence_state: PresenceState = "offline"
    last_seen_at: datetime | None = None
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
    # Viewer-relative inbox state (the requesting user's own participant flags),
    # so clients can render pinned/archived/folder grouping without a second call.
    notification_level: NotificationLevel = "all"
    muted_until: datetime | None = None
    pinned: bool = False
    archived: bool = False
    folder: str | None = None
    created_at: datetime
    updated_at: datetime


class ThreadConversationView(BaseModel):
    thread: ConversationView
    parent: ConversationView | None = None
    root_message: MessageDoc | None = None
    locked: bool = False
    converted_to_conversation_id: str | None = None


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
    draft_text: str | None = None
    draft_updated_at: datetime | None = None
    muted: bool = False
    hidden: bool = False


class CreateDmRequest(BaseModel):
    peer_user_id: str


class CreateGroupRequest(BaseModel):
    title: str = Field(min_length=1, max_length=80)
    participant_ids: list[str] = Field(min_length=1, max_length=100)
    space_id: str | None = None


class ConvertThreadToGroupRequest(BaseModel):
    title: str = Field(min_length=1, max_length=80)
    participant_ids: list[str] = Field(min_length=1, max_length=100)


class ConvertThreadToGroupResponse(BaseModel):
    group: ConversationView
    thread: ConversationView
    imported_count: int = Field(ge=0)
    truncated: bool = False


SLUG_PATTERN = r"^[a-z0-9](?:[a-z0-9-]{1,78}[a-z0-9])$"


class CreateChannelRequest(BaseModel):
    """Create a broadcast/public channel conversation."""

    title: str = Field(min_length=1, max_length=80)
    participant_ids: list[str] = Field(default_factory=list, max_length=100)
    description: str | None = Field(default=None, max_length=500)
    visibility: ConversationVisibility = "private"
    posting_policy: PostingPolicy = "admins"
    slug: str | None = Field(default=None, pattern=SLUG_PATTERN)
    space_id: str | None = None

    @model_validator(mode="after")
    def validate_public_slug(self) -> "CreateChannelRequest":
        if self.visibility == "public" and not self.slug:
            raise ValueError("slug is required for public conversations")
        return self


class UpdateGroupRequest(BaseModel):
    title: str = Field(min_length=1, max_length=80)


class UpdateConversationSettingsRequest(BaseModel):
    """Whitelisted conversation settings mutations (group/channel, owner/admin)."""

    allow_member_polls: bool


class AddGroupMembersRequest(BaseModel):
    participant_ids: list[str] = Field(min_length=1, max_length=100)


class UpdateParticipantRoleRequest(BaseModel):
    role: Literal["admin", "member"]


class UpdateInboxStateRequest(BaseModel):
    """Per-participant inbox flags. Only provided fields are applied; passing
    ``folder: null`` explicitly clears the folder assignment."""

    pinned: bool | None = None
    archived: bool | None = None
    folder: str | None = Field(default=None, max_length=80)

    @model_validator(mode="after")
    def require_some_field(self) -> "UpdateInboxStateRequest":
        if not self.model_fields_set:
            raise ValueError("At least one of pinned, archived, folder is required")
        return self


class BulkInboxStateRequest(BaseModel):
    """Apply the same inbox flags to several conversations for the caller.

    Only provided flags are applied; passing ``folder: null`` explicitly clears
    the folder assignment on every listed conversation."""

    conversation_ids: list[str] = Field(min_length=1, max_length=100)
    pinned: bool | None = None
    archived: bool | None = None
    folder: str | None = Field(default=None, max_length=80)

    @model_validator(mode="after")
    def require_some_flag(self) -> "BulkInboxStateRequest":
        if not ({"pinned", "archived", "folder"} & self.model_fields_set):
            raise ValueError("At least one of pinned, archived, folder is required")
        return self


class BulkInboxStateResult(BaseModel):
    updated: int


class FolderView(BaseModel):
    """A user's folder, discovered from per-participant ``folder`` labels."""

    name: str
    count: int
    archived_count: int


class RenameFolderRequest(BaseModel):
    new_name: str = Field(min_length=1, max_length=80)


class RenameFolderResult(BaseModel):
    updated: int


class TransferOwnershipRequest(BaseModel):
    user_id: str


class UpdateParticipantPermissionsRequest(BaseModel):
    """Set or clear a member's granular permissions map. ``null`` clears it,
    restoring pure role-based authorization."""

    permissions: dict[str, bool] | None = None


class CreateInviteRequest(BaseModel):
    expires_at: datetime | None = None
    max_uses: int | None = Field(default=None, ge=1, le=100000)
    requires_approval: bool = False


class InviteLinkView(BaseModel):
    id: StrId
    conversation_id: StrId
    code: str
    created_by: StrId
    expires_at: datetime | None = None
    max_uses: int | None = None
    use_count: int = 0
    requires_approval: bool = False
    revoked: bool = False
    created_at: datetime
    updated_at: datetime


class JoinRequestView(BaseModel):
    id: StrId
    conversation_id: StrId
    user_id: StrId
    status: Literal["pending", "approved", "rejected"]
    invite_code: str | None = None
    responded_at: datetime | None = None
    created_at: datetime


class RedeemInviteResponse(BaseModel):
    status: Literal["joined", "pending"]
    conversation: ConversationView | None = None
    join_request: JoinRequestView | None = None


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
