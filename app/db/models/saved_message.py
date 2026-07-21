from __future__ import annotations

from datetime import datetime

from pymongo import ASCENDING, DESCENDING, IndexModel

from app.db.collections import COL_SAVED_MESSAGES
from app.db.document import TimestampedDocument
from app.db.object_id import StrId


class SavedMessageDocument(TimestampedDocument):
    """A user's private bookmark of a message they can access."""

    user_id: StrId
    message_id: StrId
    conversation_id: str
    saved_at: datetime

    class Settings:
        name = COL_SAVED_MESSAGES
        indexes = [
            IndexModel(
                [("user_id", ASCENDING), ("message_id", ASCENDING)],
                unique=True,
                name="ux_saved_messages_user_message",
            ),
            IndexModel(
                [("user_id", ASCENDING), ("saved_at", DESCENDING)],
                name="ix_saved_messages_user_savedAt_desc",
            ),
        ]
