from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field


MessageType = Literal["voice", "text", "image", "emoji", "sticker", "video"]
MessageStatus = Literal["sent", "delivered", "read"]
StorageProvider = Literal["local", "s3"]


class MediaMeta(BaseModel):
    storage: StorageProvider
    key: str
    url: str
    mime: str
    size_bytes: int = Field(ge=0)
    duration_ms: Optional[int] = Field(default=None, ge=0)


class MessageDoc(BaseModel):
    id: str
    conversation_id: str
    sender_id: str
    receiver_id: str

    type: MessageType = "text"
    text: Optional[str] = None
    media: Optional[MediaMeta] = None

    status: MessageStatus = "sent"
    edited_at: Optional[datetime] = None
    delivered_at: Optional[datetime] = None
    read_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime


class SendTextMessageRequest(BaseModel):
    receiver_id: str
    text: str = Field(min_length=1, max_length=4000)


class ConversationPeer(BaseModel):
    id: str
    username: str | None = None
    display_name: str | None = None
    avatar: dict | None = None
    is_online: bool = False


class ConversationLastMessage(BaseModel):
    id: str
    type: str
    text: str | None = None
    media: dict | None = None
    status: MessageStatus = "sent"
    created_at: datetime


class ConversationItem(BaseModel):
    conversation_id: str
    peer_user: ConversationPeer
    last_message: ConversationLastMessage
    last_message_at: datetime
