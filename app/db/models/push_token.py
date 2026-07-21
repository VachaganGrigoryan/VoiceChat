from __future__ import annotations

from typing import Literal

from pymongo import ASCENDING, IndexModel

from app.db.collections import COL_PUSH_TOKENS
from app.db.document import TimestampedDocument
from app.db.object_id import StrId


class PushTokenDocument(TimestampedDocument):
    """A device push token targeted for notification delivery."""

    user_id: StrId
    device_id: str | None = None
    platform: Literal["ios", "android", "web"]
    token: str

    class Settings:
        name = COL_PUSH_TOKENS
        indexes = [
            IndexModel([("token", ASCENDING)], unique=True, name="ux_push_tokens_token"),
            IndexModel([("user_id", ASCENDING)], name="ix_push_tokens_user_id"),
        ]
