from __future__ import annotations

from datetime import datetime
from typing import Literal

from pymongo import ASCENDING, DESCENDING, IndexModel

from app.db.collections import COL_SPACE_MEMBERS
from app.db.document import TimestampedDocument
from app.db.object_id import StrId


class SpaceMemberDocument(TimestampedDocument):
    """Per-(space, user) membership and role for a workspace/space."""

    space_id: StrId
    user_id: StrId
    role: Literal["owner", "admin", "member"] = "member"
    joined_at: datetime

    class Settings:
        name = COL_SPACE_MEMBERS
        indexes = [
            IndexModel(
                [("space_id", ASCENDING), ("user_id", ASCENDING)],
                unique=True,
                name="ux_space_members_space_user",
            ),
            IndexModel(
                [("user_id", ASCENDING), ("updated_at", DESCENDING)],
                name="ix_space_members_user_updatedAt_desc",
            ),
        ]
