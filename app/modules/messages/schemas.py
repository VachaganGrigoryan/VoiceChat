from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, computed_field, model_validator

from app.db.models.message import MessageContainerType
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


class PollRef(BaseModel):
    poll_id: StrId
    question: str


class MessageTextStyle(BaseModel):
    """Presentation for a short text body. Optional and additive."""

    background: Optional[str] = Field(default=None, max_length=40)
    align: Optional[Literal["start", "center"]] = None


class MessagePlaintext(BaseModel):
    text: Optional[str] = None
    media: Optional[MediaMeta] = None
    call: Optional[CallMeta] = None
    poll: Optional[dict[str, object]] = None
    poll_ref: Optional[PollRef] = None
    sticker: Optional[dict[str, object]] = None
    location: Optional[dict[str, object]] = None
    contact: Optional[dict[str, object]] = None
    link_preview: Optional[dict[str, object]] = None
    style: Optional[MessageTextStyle] = None


class MessageContent(BaseModel):
    """Canonical encryption-ready message body."""

    encryption: EncryptionMode = "none"
    type: ContentType = "text"
    plaintext: Optional[MessagePlaintext] = None
    attachments: list[MediaMeta] = Field(default_factory=list)
    ciphertext: Optional[str] = None
    envelope: Optional[dict[str, Any]] = None


class MediaAttachmentInput(BaseModel):
    kind: MediaKind
    storage: StorageProvider = "local"
    key: str = Field(min_length=1, max_length=512)
    mime: str = Field(min_length=1, max_length=255)
    size_bytes: int = Field(ge=0)
    duration_ms: Optional[int] = Field(default=None, ge=0)


class PollOptionInput(BaseModel):
    id: str = Field(min_length=1, max_length=80)
    text: str = Field(min_length=1, max_length=200)


class PollContentInput(BaseModel):
    question: str = Field(min_length=1, max_length=500)
    options: list[PollOptionInput] = Field(min_length=2, max_length=10)
    allows_multiple: bool = False


class StickerContentInput(BaseModel):
    url: str | None = Field(default=None, max_length=2048)
    emoji: str | None = Field(default=None, max_length=32)
    label: str | None = Field(default=None, max_length=120)


class LocationContentInput(BaseModel):
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    name: str | None = Field(default=None, max_length=120)
    address: str | None = Field(default=None, max_length=240)


class ContactContentInput(BaseModel):
    display_name: str = Field(min_length=1, max_length=120)
    user_id: str | None = Field(default=None, max_length=80)
    phone: str | None = Field(default=None, max_length=80)
    email: str | None = Field(default=None, max_length=255)


class LinkPreviewContentInput(BaseModel):
    url: str = Field(min_length=1, max_length=2048)
    title: str | None = Field(default=None, max_length=200)
    description: str | None = Field(default=None, max_length=500)
    image_url: str | None = Field(default=None, max_length=2048)


class SendRichContentRequest(BaseModel):
    # Polls are created via the dedicated /polls endpoint (they link a message to a
    # first-class poll entity rather than embedding poll data here).
    type: Literal["sticker", "voice", "location", "contact", "link_preview"]
    text: str | None = Field(default=None, max_length=4000)
    attachments: list[MediaAttachmentInput] = Field(default_factory=list, max_length=10)
    sticker: StickerContentInput | None = None
    location: LocationContentInput | None = None
    contact: ContactContentInput | None = None
    link_preview: LinkPreviewContentInput | None = None
    reply_mode: Optional[ReplyMode] = None
    reply_to_message_id: Optional[str] = None

    @model_validator(mode="after")
    def require_payload(self) -> "SendRichContentRequest":
        payload = getattr(self, self.type if self.type != "voice" else "attachments")
        if self.type == "voice":
            if not self.attachments or self.attachments[0].kind != "voice":
                raise ValueError("voice content requires a voice attachment")
            return self
        if payload is None:
            raise ValueError(f"{self.type} content payload is required")
        return self


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


class ContainerEnvelope(BaseModel):
    """The `{ container_type, container_id }` envelope every message-shaped
    payload carries (§77–78).

    `conversation_id` is the compatibility mirror clients still read; it goes
    away with `fe-unified-data-layer`.
    """

    container_type: MessageContainerType
    container_id: str

    @computed_field  # type: ignore[prop-decorator]
    @property
    def conversation_id(self) -> str:
        return self.container_id


class MessageDoc(ContainerEnvelope):
    id: StrId
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


class DeleteMessageResponse(ContainerEnvelope):
    message_id: StrId
    actor_user_id: StrId
    deleted_for_everyone: bool = False
    hidden_for_me: bool = False
    deleted_media: bool = False


class PinnedMessagesView(ContainerEnvelope):
    """A container's pinned set, shaped the same for both container types.

    Pinning used to answer with a whole `ConversationView`, which is why it
    could not answer at all for a channel. The pinned set is what the caller
    asked to change, so that is what it gets back.
    """

    pinned_message_ids: list[str] = Field(default_factory=list)


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


class AddReactionRequest(BaseModel):
    emoji: str = Field(min_length=1, max_length=32)


class ThreadSummary(ContainerEnvelope):
    thread_root_id: StrId
    is_thread_root: bool = False
    thread_reply_count: int = Field(default=0, ge=0)
    last_thread_reply_at: Optional[datetime] = None


class ClearChatResponse(BaseModel):
    conversation_id: str
    cleared_count: int


class DeleteChatResponse(BaseModel):
    conversation_id: str
    cleared_count: int
