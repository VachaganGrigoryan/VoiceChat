from __future__ import annotations

from datetime import datetime

from pymongo import ASCENDING, DESCENDING, IndexModel

from app.db.collections import COL_MESSAGE_RECEIPTS
from app.db.document import TimestampedDocument
from app.db.object_id import StrId


class MessageReceiptDocument(TimestampedDocument):
    conversation_id: StrId
    message_id: StrId
    user_id: StrId
    delivered_at: datetime | None = None
    read_at: datetime | None = None

    class Settings:
        name = COL_MESSAGE_RECEIPTS
        indexes = [
            IndexModel(
                [("message_id", ASCENDING), ("user_id", ASCENDING)],
                unique=True,
                name="ux_message_receipts_message_user",
            ),
            IndexModel(
                [
                    ("conversation_id", ASCENDING),
                    ("user_id", ASCENDING),
                    ("read_at", DESCENDING),
                ],
                name="ix_message_receipts_conversation_user_readAt",
            ),
            IndexModel(
                [("conversation_id", ASCENDING), ("message_id", ASCENDING)],
                name="ix_message_receipts_conversation_message",
            ),
        ]
