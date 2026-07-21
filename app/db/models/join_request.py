from __future__ import annotations

from datetime import datetime
from typing import Literal

from pymongo import ASCENDING, IndexModel

from app.db.collections import COL_JOIN_REQUESTS
from app.db.document import TimestampedDocument
from app.db.object_id import StrId


class JoinRequestDocument(TimestampedDocument):
    """A pending request to join a conversation or space via an approval-gated invite."""

    target_type: Literal["conversation", "space"]
    target_id: StrId
    user_id: StrId
    status: Literal["pending", "approved", "rejected"] = "pending"
    invite_code: str | None = None
    responded_at: datetime | None = None

    class Settings:
        name = COL_JOIN_REQUESTS
        indexes = [
            IndexModel(
                [
                    ("target_type", ASCENDING),
                    ("target_id", ASCENDING),
                    ("status", ASCENDING),
                ],
                name="ix_join_requests_target_status",
            ),
            IndexModel([("user_id", ASCENDING)], name="ix_join_requests_user_id"),
        ]
