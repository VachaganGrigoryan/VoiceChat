from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field
from pymongo import ASCENDING, IndexModel

from app.db.document import BaseDocument
from app.db.collections import COL_DISCOVERY_TOKENS
from app.db.object_id import StrId


class DiscoveryTokenDocument(BaseDocument):
    user_id: StrId
    type: Literal["code", "link"]
    token_hash: str
    token_preview: str | None = None
    is_active: bool = True
    expires_at: datetime | None = None
    max_uses: int | None = Field(default=None, ge=1)
    use_count: int = Field(default=0, ge=0)
    used_at: datetime | None = None
    created_at: datetime
    updated_at: datetime

    class Settings:
        name = COL_DISCOVERY_TOKENS
        indexes = [
            IndexModel([("user_id", ASCENDING)], name="idx_discovery_user_id"),
            IndexModel([("type", ASCENDING)], name="idx_discovery_type"),
            IndexModel([("expires_at", ASCENDING)], name="idx_discovery_expires_at"),
            IndexModel([("is_active", ASCENDING)], name="idx_discovery_is_active"),
            IndexModel(
                [("user_id", ASCENDING), ("type", ASCENDING), ("is_active", ASCENDING)],
                partialFilterExpression={"type": "code", "is_active": True},
                name="uniq_active_code_per_user",
            ),
        ]
