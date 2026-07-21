from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import Field
from pymongo import ASCENDING, DESCENDING, IndexModel

from app.db.collections import COL_AUDIT_LOGS
from app.db.document import BaseDocument
from app.db.object_id import StrId


class AuditLogDocument(BaseDocument):
    """Append-only trail of sensitive administrative actions.

    Extensibility seam (``chat-extensibility``): capture only; retention and
    export tooling are deferred.
    """

    actor_id: StrId
    action: str
    target_type: str | None = None
    target_id: StrId | None = None
    space_id: StrId | None = None
    data: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    class Settings:
        name = COL_AUDIT_LOGS
        indexes = [
            IndexModel(
                [("actor_id", ASCENDING), ("created_at", DESCENDING)],
                name="ix_audit_logs_actor_createdAt_desc",
            ),
            IndexModel(
                [("target_type", ASCENDING), ("target_id", ASCENDING)],
                name="ix_audit_logs_target",
            ),
        ]
