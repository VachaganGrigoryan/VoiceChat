from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import Field
from pymongo import ASCENDING, DESCENDING, IndexModel

from app.db.collections import COL_CONVERSATIONS
from app.db.document import TimestampedDocument
from app.db.models.embedded import ConversationPreviewDocument
from app.db.object_id import StrId


class ConversationDocument(TimestampedDocument):
    """First-class conversation entity.

    Replaces the derived ``sorted("{a}_{b}")`` conversation id. Modeled for N
    participants (``type == "group"``), but the service layer enforces DM-only for
    now. ``dm_key`` reuses the legacy sorted pair string and is uniquely indexed
    (partial, ``type == "dm"``) to guarantee one DM per pair.
    """

    type: Literal["dm", "group"] = "dm"
    participant_ids: list[StrId] = Field(default_factory=list)
    created_by: StrId
    title: str | None = None
    image: dict[str, Any] | None = None
    encryption: Literal["none", "e2ee"] = "none"
    dm_key: str | None = None
    last_message_at: datetime | None = None
    last_message_preview: ConversationPreviewDocument | None = None

    class Settings:
        name = COL_CONVERSATIONS
        indexes = [
            IndexModel(
                [("dm_key", ASCENDING)],
                unique=True,
                partialFilterExpression={"type": "dm"},
                name="ux_conversations_dm_key",
            ),
            IndexModel(
                [("participant_ids", ASCENDING)],
                name="ix_conversations_participant_ids",
            ),
            IndexModel(
                [("last_message_at", DESCENDING)],
                name="ix_conversations_last_message_at_desc",
            ),
        ]
