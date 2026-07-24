from __future__ import annotations

from pymongo import ASCENDING, IndexModel

from app.db.collections import COL_SLASH_COMMANDS
from app.db.document import TimestampedDocument
from app.db.object_id import StrId


class SlashCommandDocument(TimestampedDocument):
    """A registered slash command and its handler target target.

    Extensibility seam (``chat-extensibility``): model + registry only; command
    execution and interaction dispatch are deferred.
    """

    trigger: str  # e.g., "poll" (maps to /poll)
    handler_url: str
    description: str | None = None
    created_by: StrId
    active: bool = True

    class Settings:
        name = COL_SLASH_COMMANDS
        indexes = [
            IndexModel(
                [("trigger", ASCENDING)],
                unique=True,
                name="ux_slash_commands_trigger",
            ),
        ]
