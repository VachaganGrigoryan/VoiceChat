from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field


MessageType = Literal["voice"]
MessageStatus = Literal["sent", "delivered", "read"]
StorageProvider = Literal["local", "s3"]


class AudioMeta(BaseModel):
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
    type: MessageType = "voice"
    audio: AudioMeta
    status: MessageStatus = "sent"
    delivered_at: Optional[datetime] = None
    read_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime