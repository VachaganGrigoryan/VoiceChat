from __future__ import annotations

from pymongo import ASCENDING, IndexModel

from app.db.collections import COL_BLOCKS
from app.db.document import TimestampedDocument
from app.db.object_id import StrId


class BlockDocument(TimestampedDocument):
    """A unidirectional block: ``blocker_id`` has blocked ``blocked_id``."""

    blocker_id: StrId
    blocked_id: StrId

    class Settings:
        name = COL_BLOCKS
        indexes = [
            IndexModel(
                [("blocker_id", ASCENDING), ("blocked_id", ASCENDING)],
                unique=True,
                name="ux_blocks_blocker_blocked",
            ),
            IndexModel([("blocked_id", ASCENDING)], name="ix_blocks_blocked_id"),
        ]
