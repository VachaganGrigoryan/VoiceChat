from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.db.models import MessageContainerType
from app.db.object_id import StrId
from app.modules.messages.schemas import MessageDoc


class SaveMessageRequest(BaseModel):
    container_type: MessageContainerType
    container_id: str = Field(min_length=1)
    message_id: str = Field(min_length=1)


class SavedMessageView(BaseModel):
    id: StrId
    user_id: StrId
    message_id: StrId
    container_type: MessageContainerType | None = None
    container_id: str | None = None
    saved_at: datetime
    message: MessageDoc | None = None
