from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field, model_validator

MessageType = Literal["voice", "text", "image", "emoji", "sticker", "video"]
MessageStatus = Literal["sent", "delivered", "read"]
StorageProvider = Literal["local", "s3"]
ReplyMode = Literal["quote", "thread"]


class MediaMeta(BaseModel):
    storage: StorageProvider
    key: str
    url: str
    mime: str
    size_bytes: int = Field(ge=0)
    duration_ms: Optional[int] = Field(default=None, ge=0)


class StickerMessageRef(BaseModel):
    sticker_id: str
    pack_id: str
    pack_slug: str
    sticker_slug: str
    emoji: str | None = None
    version: int = Field(default=1, ge=1)


class ReplyPreview(BaseModel):
    message_id: str
    sender_id: str
    type: MessageType
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
    sticker: Optional[StickerMessageRef] = None

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


class SendStickerMessageRequest(BaseModel):
    receiver_id: str
    sticker_id: str
    emoji: str | None = None
    reply_mode: Optional[ReplyMode] = None
    reply_to_message_id: Optional[str] = None

    @model_validator(mode="after")
    def validate_reply_fields(self) -> "SendStickerMessageRequest":
        if self.reply_mode and not self.reply_to_message_id:
            raise ValueError("reply_to_message_id is required when reply_mode is set")
        if self.reply_to_message_id and not self.reply_mode:
            raise ValueError("reply_mode is required when reply_to_message_id is set")
        return self


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


class ConversationLastMessage(BaseModel):
    id: str
    type: str
    text: str | None = None
    media: MediaMeta | None = None
    sticker: StickerMessageRef | None = None
    status: MessageStatus = "sent"
    created_at: datetime


class ConversationItem(BaseModel):
    conversation_id: str
    peer_user: ConversationPeer
    last_message: ConversationLastMessage
    last_message_at: datetime
    unread_count: int = 0
