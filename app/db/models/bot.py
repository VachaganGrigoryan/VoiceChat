from __future__ import annotations

from pymongo import ASCENDING, IndexModel

from app.db.collections import COL_BOTS
from app.db.document import TimestampedDocument
from app.db.object_id import StrId


class BotDocument(TimestampedDocument):
    """A bot account backed by a distinct user, badged so clients can mark it.

    User-created bots carry an `owner_user_id`; built-in bots (e.g. PollBot) set
    `builtin = True` and are identified by a stable `slug` instead of an owner.
    """

    user_id: StrId
    owner_user_id: StrId | None = None
    name: str
    description: str | None = None
    token_hash: str | None = None
    builtin: bool = False
    slug: str | None = None

    class Settings:
        name = COL_BOTS
        indexes = [
            IndexModel([("user_id", ASCENDING)], unique=True, name="ux_bots_user_id"),
            IndexModel(
                [("owner_user_id", ASCENDING)], name="ix_bots_owner_user_id"
            ),
            # Built-in bots are keyed by slug; sparse so non-built-in bots
            # (slug = None) are not indexed and can coexist freely.
            IndexModel(
                [("slug", ASCENDING)],
                unique=True,
                sparse=True,
                name="ux_bots_slug",
            ),
        ]
