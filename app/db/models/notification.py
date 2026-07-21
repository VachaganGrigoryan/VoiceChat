from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import Field
from pymongo import ASCENDING, DESCENDING, IndexModel

from app.db.collections import COL_NOTIFICATIONS
from app.db.document import TimestampedDocument
from app.db.object_id import StrId


class NotificationDocument(TimestampedDocument):
    """A per-user notification feed entry (message, mention, join request, ...)."""

    user_id: StrId
    kind: str
    source_type: str | None = None
    source_id: StrId | None = None
    conversation_id: str | None = None
    read_at: datetime | None = None
    data: dict[str, Any] = Field(default_factory=dict)

    class Settings:
        name = COL_NOTIFICATIONS
        indexes = [
            IndexModel(
                [("user_id", ASCENDING), ("created_at", DESCENDING)],
                name="ix_notifications_user_createdAt_desc",
            ),
            IndexModel(
                [("user_id", ASCENDING), ("read_at", ASCENDING)],
                name="ix_notifications_user_readAt",
            ),
        ]
