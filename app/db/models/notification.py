from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import Field
from pymongo import ASCENDING, DESCENDING, IndexModel

from app.db.collections import COL_NOTIFICATIONS
from app.db.document import TimestampedDocument
from app.db.object_id import StrId

NotificationKind = Literal[
    "connection_request",
    "connection_accepted",
    "follow",
    "follow_request",
    "membership_invite",
    "membership_approved",
    "message",
    "mention",
    "comment",
    "comment_reply",
    "thread_reply",
    "reaction",
]
NotificationResourceType = Literal["user", "conversation", "channel", "space"]


class NotificationDocument(TimestampedDocument):
    """A per-user notification feed entry targeting any first-class resource."""

    user_id: StrId
    kind: NotificationKind
    actor_user_id: StrId
    resource_type: NotificationResourceType
    resource_id: StrId
    message_id: StrId | None = None
    read_at: datetime | None = None
    data: dict[str, Any] = Field(default_factory=dict)

    class Settings:
        name = COL_NOTIFICATIONS
        indexes = [
            IndexModel(
                [("user_id", ASCENDING), ("created_at", DESCENDING)],
                name="ix_notifications_user_createdAt_desc",
            ),
            IndexModel(
                [("user_id", ASCENDING), ("read_at", ASCENDING)],
                name="ix_notifications_user_readAt",
            ),
            IndexModel(
                [
                    ("resource_type", ASCENDING),
                    ("resource_id", ASCENDING),
                    ("created_at", DESCENDING),
                ],
                name="ix_notifications_resource_createdAt_desc",
            ),
        ]
