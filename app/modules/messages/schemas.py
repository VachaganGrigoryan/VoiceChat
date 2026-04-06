from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field, model_validator

MessageType = Literal["text", "media", "file", "call"]
MediaKind = Literal["voice", "audio", "image", "video", "file"]
MessageStatus = Literal["sent", "delivered", "read"]
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
    caller_user_id: str
    callee_user_id: str
    started_at: datetime
    answered_at: datetime | None = None
    ended_at: datetime | None = None
    duration_ms: int = Field(default=0, ge=0)


class ReplyPreview(BaseModel):
    message_id: str
    sender_id: str
    type: MessageType
    media_kind: Optional[MediaKind] = None
    text: Optional[str] = None
    is_deleted: bool = False


class MessageReactionGroup(BaseModel):
    emoji: str
    user_ids: list[str] = Field(default_factory=list)
    count: int = Field(ge=0)
    updated_at: datetime


class MessageDoc(BaseModel):
    id: str
    conversation_id: str
    sender_id: str
    receiver_id: str

    type: MessageType = "text"
    text: Optional[str] = None
    media: Optional[MediaMeta] = None
    call: Optional[CallMeta] = None

    status: MessageStatus = "sent"
    edited_at: Optional[datetime] = None
    delivered_at: Optional[datetime] = None
    read_at: Optional[datetime] = None

    is_deleted: bool = False

    reply_mode: Optional[ReplyMode] = None
    reply_to_message_id: Optional[str] = None
    thread_root_id: Optional[str] = None
    reply_preview: Optional[ReplyPreview] = None

    is_thread_root: bool = False
    thread_reply_count: int = Field(default=0, ge=0)
    last_thread_reply_at: Optional[datetime] = None
    reactions: list[MessageReactionGroup] = Field(default_factory=list)

    created_at: datetime
    updated_at: datetime


class DeleteMessageResponse(BaseModel):
    message_id: str
    conversation_id: str
    actor_user_id: str
    deleted_for_everyone: bool = False
    hidden_for_me: bool = False
    deleted_media: bool = False


class MessageDeleteOutcome(BaseModel):
    response: DeleteMessageResponse
    sender_id: str
    receiver_id: str


class SendTextMessageRequest(BaseModel):
    receiver_id: str
    text: str = Field(min_length=1, max_length=4000)
    reply_mode: Optional[ReplyMode] = None
    reply_to_message_id: Optional[str] = None

    @model_validator(mode="after")
    def validate_reply_fields(self) -> "SendTextMessageRequest":
        if self.reply_mode and not self.reply_to_message_id:
            raise ValueError("reply_to_message_id is required when reply_mode is set")
        if self.reply_to_message_id and not self.reply_mode:
            raise ValueError("reply_mode is required when reply_to_message_id is set")
        return self


class EditMessageRequest(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


class UpdateMessageStatusRequest(BaseModel):
    # useful if later you want one REST endpoint instead of two
    status: Literal["delivered", "read"]


class MarkConversationReadRequest(BaseModel):
    peer_user_id: str


class AddReactionRequest(BaseModel):
    emoji: str = Field(min_length=1, max_length=32)


class ThreadSummary(BaseModel):
    thread_root_id: str
    conversation_id: str
    is_thread_root: bool = False
    thread_reply_count: int = Field(default=0, ge=0)
    last_thread_reply_at: Optional[datetime] = None


class ConversationPeer(BaseModel):
    id: str
    username: str | None = None
    display_name: str | None = None
    avatar: dict | None = None
    is_online: bool = False
    can_ping: bool = False
    chat_allowed: bool = False
    ping_status: str = "none"
    is_ghost: bool = False


class ConversationLastMessage(BaseModel):
    id: str
    type: MessageType
    text: str | None = None
    media: MediaMeta | None = None
    call: CallMeta | None = None
    status: MessageStatus = "sent"
    created_at: datetime


class ClearChatResponse(BaseModel):
    conversation_id: str
    cleared_count: int


class DeleteChatResponse(BaseModel):
    conversation_id: str
    cleared_count: int
    ping_deleted: bool


class ConversationItem(BaseModel):
    conversation_id: str
    peer_user: ConversationPeer
    last_message: ConversationLastMessage
    last_message_at: datetime
    unread_count: int = 0
