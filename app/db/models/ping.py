from __future__ import annotations

from datetime import datetime
from typing import Literal

from pymongo import ASCENDING, DESCENDING, IndexModel

from app.db.document import BaseDocument
from app.db.collections import COL_PINGS
from app.db.object_id import StrId


class PingDocument(BaseDocument):
    pair_id: str
    from_user_id: StrId
    to_user_id: StrId
    status: Literal[
        "pending", "accepted", "declined", "cancelled", "expired", "blocked"
    ]
    created_at: datetime
    updated_at: datetime
    responded_at: datetime | None = None

    class Settings:
        name = COL_PINGS
        indexes = [
            IndexModel(
                [("pair_id", ASCENDING)], unique=True, name="uniq_pings_pair_id"
            ),
            IndexModel([("from_user_id", ASCENDING)], name="idx_pings_from_user_id"),
            IndexModel([("to_user_id", ASCENDING)], name="idx_pings_to_user_id"),
            IndexModel([("status", ASCENDING)], name="idx_pings_status"),
            IndexModel([("updated_at", ASCENDING)], name="idx_pings_updated_at"),
            IndexModel(
                [
                    ("to_user_id", ASCENDING),
                    ("created_at", DESCENDING),
                    ("_id", DESCENDING),
                ],
                name="idx_pings_incoming_cursor",
            ),
            IndexModel(
                [
                    ("from_user_id", ASCENDING),
                    ("created_at", DESCENDING),
                    ("_id", DESCENDING),
                ],
                name="idx_pings_outgoing_cursor",
            ),
            IndexModel(
                [("pair_id", ASCENDING), ("status", ASCENDING)],
                name="ix_pings_pair_status",
            ),
        ]
