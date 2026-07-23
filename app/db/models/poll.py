from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field
from pymongo import ASCENDING, IndexModel

from app.db.collections import COL_POLLS
from app.db.document import EmbeddedBase, TimestampedDocument
from app.db.object_id import StrId

PollResultsVisibility = Literal["after_vote", "always", "after_close"]


class PollOptionDocument(EmbeddedBase):
    """A single selectable poll choice with a stable id."""

    id: str
    text: str


class PollVoteDocument(EmbeddedBase):
    """One user's current selection on a poll.

    At most one entry per user (upsert on `user_id`) which yields one-vote-per-user,
    vote changes, and retraction (entry removed) in a single shape.
    """

    user_id: StrId
    option_ids: list[str] = Field(default_factory=list)
    voted_at: datetime


class PollDocument(TimestampedDocument):
    """A first-class poll owned by PollBot and linked from a chat message.

    The mutable poll state (options, votes, tallies, close status) lives here; the
    chat message only references it via ``poll_ref`` (see ``PlaintextContentDocument``).
    """

    conversation_id: StrId
    message_id: StrId | None = None
    created_by: StrId
    bot_id: StrId
    question: str
    options: list[PollOptionDocument] = Field(default_factory=list)
    allows_multiple: bool = False
    anonymous: bool = False
    results_visibility: PollResultsVisibility = "after_vote"
    closes_at: datetime | None = None
    closed: bool = False
    closed_at: datetime | None = None
    closed_by: StrId | None = None
    votes: list[PollVoteDocument] = Field(default_factory=list)

    class Settings:
        name = COL_POLLS
        indexes = [
            IndexModel(
                [("conversation_id", ASCENDING)], name="ix_polls_conversation"
            ),
            # Cheap due-poll lookup for the auto-close poller: only indexes polls
            # still open (mirrors the scheduled-message poller index).
            IndexModel(
                [("closed", ASCENDING), ("closes_at", ASCENDING)],
                partialFilterExpression={"closed": False},
                name="ix_polls_closed_closesAt",
            ),
        ]
