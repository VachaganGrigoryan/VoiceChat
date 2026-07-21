from __future__ import annotations

from datetime import datetime
from typing import Literal

from pymongo import ASCENDING, DESCENDING, IndexModel

from app.db.collections import COL_CONVERSATION_PARTICIPANTS
from app.db.document import TimestampedDocument
from app.db.object_id import StrId


class ParticipantDocument(TimestampedDocument):
    """Per-(conversation, user) membership and read state.

    Unread counts are computed from ``last_read_at`` / ``last_read_message_id``
    rather than receiver-based message scans, so the model scales to groups.
    """

    conversation_id: StrId
    user_id: StrId
    role: Literal["owner", "admin", "member", "subscriber"] = "member"
    # Optional granular rights that refine the base role (can_pin, can_invite,
    # can_delete, can_restrict, can_manage, ...). Absent => role defaults.
    permissions: dict[str, bool] | None = None
    joined_at: datetime
    last_read_at: datetime | None = None
    last_read_message_id: str | None = None
    # Notification + inbox organization (finalize-messenger-conversation-model).
    # `muted` is retained for back-compat and treated as notification_level=none.
    notification_level: Literal["all", "mentions", "none"] = "all"
    muted_until: datetime | None = None
    archived: bool = False
    pinned: bool = False
    folder: str | None = None
    invited_by: StrId | None = None
    draft_updated_at: datetime | None = None
    muted: bool = False
    hidden: bool = False

    class Settings:
        name = COL_CONVERSATION_PARTICIPANTS
        indexes = [
            IndexModel(
                [("conversation_id", ASCENDING), ("user_id", ASCENDING)],
                unique=True,
                name="ux_participants_conversation_user",
            ),
            IndexModel(
                [("user_id", ASCENDING), ("updated_at", DESCENDING)],
                name="ix_participants_user_updatedAt_desc",
            ),
        ]
