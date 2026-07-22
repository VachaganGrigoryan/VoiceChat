from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

from app.db.object_id import StrId

MessageType = Literal[
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
MediaKind = Literal["voice", "audio", "image", "video", "file"]
StorageProvider = Literal["local", "s3"]
ReplyMode = Literal["quote", "thread"]
CallMessageStatus = Literal["rejected", "cancelled", "expired", "ended"]


class MediaMeta(BaseModel):
    kind: MediaKind
    storage: StorageProvider
    key: str
    url: str
    mime: str
    size_bytes: int = Field(ge=0)
    duration_ms: Optional[int] = Field(default=None, ge=0)


class CallMeta(BaseModel):
    call_id: str
    type: Literal["audio", "video"]
    status: CallMessageStatus
    caller_user_id: StrId
    callee_user_id: StrId
    started_at: datetime
    answered_at: datetime | None = None
    ended_at: datetime | None = None
    duration_ms: int = Field(default=0, ge=0)


class ReplyPreview(BaseModel):
    message_id: StrId
    sender_id: StrId
    type: MessageType
    media_kind: Optional[MediaKind] = None
    text: Optional[str] = None
    is_deleted: bool = False


class MessageReactionGroup(BaseModel):
    emoji: str
    user_ids: list[StrId] = Field(default_factory=list)
    count: int = Field(ge=0)
    updated_at: datetime


ContentType = MessageType
EncryptionMode = Literal["none", "e2ee"]
MentionScope = Literal["here", "all"]
MessageState = Literal["sent", "scheduled"]


class MessagePlaintext(BaseModel):
    text: Optional[str] = None
    media: Optional[MediaMeta] = None
    call: Optional[CallMeta] = None


class MessageContent(BaseModel):
    """Canonical encryption-ready message body."""

    encryption: EncryptionMode = "none"
    type: ContentType = "text"
    plaintext: Optional[MessagePlaintext] = None
    attachments: list[MediaMeta] = Field(default_factory=list)
    ciphertext: Optional[str] = None
    envelope: Optional[dict[str, Any]] = None


class ForwardedFrom(BaseModel):
    conversation_id: str
    message_id: str
    sender_id: StrId
    forwarded_at: datetime


class MessageEdit(BaseModel):
    content: MessageContent
    edited_at: datetime


class MessageReceiptSummary(BaseModel):
    recipient_count: int = Field(default=0, ge=0)
    delivered_count: int = Field(default=0, ge=0)
    read_count: int = Field(default=0, ge=0)


class MessageDoc(BaseModel):
    id: StrId
    conversation_id: str
    sender_id: StrId

    type: MessageType = "text"
    content: Optional[MessageContent] = None

    receipt_summary: MessageReceiptSummary = Field(
        default_factory=MessageReceiptSummary
    )
    edited_at: Optional[datetime] = None
    edit_history: list[MessageEdit] = Field(default_factory=list)

    is_deleted: bool = False

    reply_mode: Optional[ReplyMode] = None
    reply_to_message_id: Optional[str] = None
    thread_root_id: Optional[str] = None
    reply_preview: Optional[ReplyPreview] = None

    is_thread_root: bool = False
    thread_reply_count: int = Field(default=0, ge=0)
    last_thread_reply_at: Optional[datetime] = None
    mention_user_ids: list[StrId] = Field(default_factory=list)
    mention_scope: MentionScope | None = None
    forwarded_from: ForwardedFrom | None = None
    scheduled_for: datetime | None = None
    state: MessageState = "sent"
    reactions: list[MessageReactionGroup] = Field(default_factory=list)

    created_at: datetime
    updated_at: datetime


class DeleteMessageResponse(BaseModel):
    message_id: StrId
    conversation_id: str
    actor_user_id: StrId
    deleted_for_everyone: bool = False
    hidden_for_me: bool = False
    deleted_media: bool = False


class MessageDeleteOutcome(BaseModel):
    response: DeleteMessageResponse
    sender_id: str


class EditMessageRequest(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


class ForwardMessageRequest(BaseModel):
    target_conversation_id: str = Field(min_length=1)


class ScheduleMessageRequest(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    scheduled_for: datetime


class SetDraftRequest(BaseModel):
    text: str = Field(default="", max_length=4000)


class MessageSearchResults(BaseModel):
    items: list[MessageDoc] = Field(default_factory=list)
    has_more: bool = False


class AddReactionRequest(BaseModel):
    emoji: str = Field(min_length=1, max_length=32)


class ThreadSummary(BaseModel):
    thread_root_id: StrId
    conversation_id: str
    is_thread_root: bool = False
    thread_reply_count: int = Field(default=0, ge=0)
    last_thread_reply_at: Optional[datetime] = None


class ClearChatResponse(BaseModel):
    conversation_id: str
    cleared_count: int


class DeleteChatResponse(BaseModel):
    conversation_id: str
    cleared_count: int
    ping_deleted: bool
