from __future__ import annotations

from pymongo import ASCENDING, IndexModel

from app.db.collections import COL_BOTS
from app.db.document import TimestampedDocument
from app.db.object_id import StrId


class BotDocument(TimestampedDocument):
    """A bot account backed by a distinct user, badged so clients can mark it.

    Extensibility seam (``chat-extensibility``): identity + model only; automated
    behaviors are deferred.
    """

    user_id: StrId
    owner_user_id: StrId
    name: str
    description: str | None = None
    token_hash: str | None = None

    class Settings:
        name = COL_BOTS
        indexes = [
            IndexModel([("user_id", ASCENDING)], unique=True, name="ux_bots_user_id"),
            IndexModel(
                [("owner_user_id", ASCENDING)], name="ix_bots_owner_user_id"
            ),
        ]
