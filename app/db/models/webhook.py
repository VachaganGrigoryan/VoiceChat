from __future__ import annotations

from typing import Literal

from pydantic import Field
from pymongo import ASCENDING, IndexModel

from app.db.collections import COL_WEBHOOKS
from app.db.document import TimestampedDocument
from app.db.object_id import StrId


class WebhookDocument(TimestampedDocument):
    """An incoming or outgoing webhook scoped to a conversation or space.

    Extensibility seam (``chat-extensibility``): model + scoping only; delivery
    execution and retry policy are deferred.
    """

    direction: Literal["incoming", "outgoing"]
    target_type: Literal["conversation", "space"]
    target_id: StrId
    url: str | None = None
    secret_hash: str | None = None
    created_by: StrId
    events: list[str] = Field(default_factory=list)
    active: bool = True

    class Settings:
        name = COL_WEBHOOKS
        indexes = [
            IndexModel(
                [("target_type", ASCENDING), ("target_id", ASCENDING)],
                name="ix_webhooks_target",
            ),
        ]
