from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, model_validator

from app.db.object_id import StrId
from app.modules.messages.schemas import MessageTextStyle
from app.modules.relationships.schemas import (
    ConnectionDirection,
    ConnectionStatusView,
    RelationshipView,
)
from app.modules.realtime.presence.base import PresenceState

ReplyMode = Literal["quote", "thread"]

ConversationType = Literal["dm", "group"]
EncryptionMode = Literal["none", "e2ee"]
# The name of the RoleDocument a participant holds (resource-authorization).
# Free-form because roles are data: seeded system roles are "Admin",
# "Moderator", "Member", "Guest", and a scope may define its own.
ParticipantRole = str
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
    connection_status: ConnectionStatusView | None = None
    connection_direction: ConnectionDirection | None = None
    relationship_id: StrId | None = None
    is_ghost: bool = False


class ConversationView(BaseModel):
    id: StrId
    type: ConversationType
    encryption: EncryptionMode
    participant_ids: list[StrId]
    created_by: StrId
    # Who owns this conversation. Ownership is not a role (§51), so clients read
    # it here rather than inferring it from a participant's role.
    owner_type: Literal["user", "space"] | None = None
    owner_id: StrId | None = None
    title: str | None = None
    image: dict | None = None
    visibility: ConversationVisibility = "private"
    posting_policy: PostingPolicy = "everyone"
    space_id: StrId | None = None
    space_visibility: Literal["space_public", "invite_only"] | None = None
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


class ParticipantView(BaseModel):
    conversation_id: StrId
    user_id: StrId
    role: ParticipantRole | None = None
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
    space_visibility: Literal["space_public", "invite_only"] | None = None


class UpdateGroupRequest(BaseModel):
    title: str = Field(min_length=1, max_length=80)


class UpdateConversationSettingsRequest(BaseModel):
    """Whitelisted group conversation settings mutations."""

    allow_member_polls: bool


class AddGroupMembersRequest(BaseModel):
    participant_ids: list[str] = Field(min_length=1, max_length=100)


class UpdateParticipantRoleRequest(BaseModel):
    """Assign a conversation role by name (a `roles` document in this scope)."""

    role: str = Field(min_length=1, max_length=64)


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
    approval_required: bool = False
    role_ids: list[str] = Field(default_factory=list)


class InviteLinkView(BaseModel):
    id: StrId
    conversation_id: StrId
    code: str
    created_by: StrId
    expires_at: datetime | None = None
    max_uses: int | None = None
    uses: int = 0
    approval_required: bool = False
    role_ids: list[StrId] = Field(default_factory=list)
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
    membership: RelationshipView


class ConversationSendTextRequest(BaseModel):
    """Body of a text send; the container is derived from the path."""

    text: str = Field(min_length=1, max_length=4000)
    reply_mode: Optional[ReplyMode] = None
    reply_to_message_id: Optional[str] = None
    style: Optional[MessageTextStyle] = None

    @model_validator(mode="after")
    def validate_reply_fields(self) -> "ConversationSendTextRequest":
        if self.reply_mode and not self.reply_to_message_id:
            raise ValueError("reply_to_message_id is required when reply_mode is set")
        if self.reply_to_message_id and not self.reply_mode:
            raise ValueError("reply_mode is required when reply_to_message_id is set")
        return self
