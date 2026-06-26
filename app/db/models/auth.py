from __future__ import annotations

from datetime import datetime

from pymongo import ASCENDING, DESCENDING, IndexModel

from app.db.document import BaseDocument
from app.db.collections import COL_REFRESH_TOKENS
from app.db.object_id import StrId


class RefreshTokenDocument(BaseDocument):
    user_id: StrId
    token_hash: str
    expires_at: datetime
    revoked_at: datetime | None = None
    replaced_by_token_hash: str | None = None
    created_at: datetime
    updated_at: datetime
    user_agent: str | None = None
    ip: str | None = None

    class Settings:
        name = COL_REFRESH_TOKENS
        indexes = [
            IndexModel(
                [("token_hash", ASCENDING)],
                unique=True,
                name="ux_refresh_tokens_token_hash",
            ),
            IndexModel(
                [("user_id", ASCENDING), ("created_at", DESCENDING)],
                name="ix_refresh_tokens_user_createdAt_desc",
            ),
            IndexModel(
                [("expires_at", ASCENDING)],
                expireAfterSeconds=0,
                name="ttl_refresh_tokens_expires",
            ),
        ]
