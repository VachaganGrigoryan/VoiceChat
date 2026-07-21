from __future__ import annotations

from datetime import datetime
from typing import Literal

from pymongo import ASCENDING, DESCENDING, IndexModel

from app.db.collections import COL_REPORTS
from app.db.document import TimestampedDocument
from app.db.object_id import StrId


class ReportDocument(TimestampedDocument):
    """A moderation report against a message, user, or conversation.

    Extensibility seam (``chat-extensibility``): capture + resolution only;
    automated enforcement is deferred.
    """

    target_type: Literal["message", "user", "conversation"]
    target_id: StrId
    reporter_id: StrId
    reason: str
    status: Literal["pending", "resolved", "dismissed"] = "pending"
    resolved_by: StrId | None = None
    resolved_at: datetime | None = None

    class Settings:
        name = COL_REPORTS
        indexes = [
            IndexModel(
                [("status", ASCENDING), ("created_at", DESCENDING)],
                name="ix_reports_status_createdAt_desc",
            ),
            IndexModel(
                [("target_type", ASCENDING), ("target_id", ASCENDING)],
                name="ix_reports_target",
            ),
        ]
