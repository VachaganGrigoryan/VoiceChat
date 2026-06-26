from __future__ import annotations

from datetime import datetime

from pydantic import Field
from pymongo import ASCENDING, IndexModel

from app.db.document import BaseDocument
from app.db.collections import COL_VERIFICATION
from app.db.object_id import StrId


class VerificationCodeDocument(BaseDocument):
    method: str
    identifier: str
    user_id: StrId
    purpose: str
    code_hash: str
    attempts: int = Field(default=0, ge=0)
    expires_at: datetime
    created_at: datetime

    class Settings:
        name = COL_VERIFICATION
        indexes = [
            IndexModel(
                [("expires_at", ASCENDING)],
                expireAfterSeconds=0,
                name="ttl_verification_expires",
            ),
            IndexModel(
                [
                    ("method", ASCENDING),
                    ("identifier", ASCENDING),
                    ("purpose", ASCENDING),
                ],
                name="ix_verification_method_identifier_purpose",
            ),
            IndexModel(
                [("user_id", ASCENDING), ("method", ASCENDING), ("purpose", ASCENDING)],
                name="ix_verification_user_method_purpose",
            ),
        ]
