from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import Field
from pymongo import ASCENDING, DESCENDING, IndexModel

from app.db.document import BaseDocument
from app.db.collections import COL_CALLS
from app.db.models.embedded import CallParticipantStateDocument
from app.db.object_id import StrId


class CallDocument(BaseDocument):
    caller_user_id: StrId
    callee_user_id: StrId
    participant_user_ids: list[StrId] = Field(min_length=2, max_length=2)
    type: Literal["audio", "video"]
    status: Literal[
        "ringing",
        "accepted",
        "connecting",
        "active",
        "reconnecting",
        "rejected",
        "cancelled",
        "expired",
        "ended",
    ]
    room_id: str
    created_at: datetime
    updated_at: datetime
    answered_at: datetime | None = None
    ended_at: datetime | None = None
    expires_at: datetime | None = None
    reconnect_deadline_at: datetime | None = None
    disconnected_user_ids: list[StrId] = Field(default_factory=list)
    participant_states: dict[str, CallParticipantStateDocument | dict[str, Any]] = (
        Field(default_factory=dict)
    )
    hidden_for_user_ids: list[StrId] = Field(default_factory=list)
    is_live: bool = True
    history_message_id: StrId | None = None

    class Settings:
        name = COL_CALLS
        indexes = [
            IndexModel(
                [("participant_user_ids", ASCENDING)],
                unique=True,
                partialFilterExpression={"is_live": True},
                name="ux_calls_live_participant",
            ),
            IndexModel(
                [("status", ASCENDING), ("expires_at", ASCENDING)],
                name="ix_calls_status_expires_at",
            ),
            IndexModel(
                [("status", ASCENDING), ("reconnect_deadline_at", ASCENDING)],
                name="ix_calls_status_reconnect_deadline_at",
            ),
            IndexModel(
                [("participant_user_ids", ASCENDING), ("created_at", DESCENDING)],
                name="ix_calls_participant_created_at_desc",
            ),
            IndexModel(
                [
                    ("participant_user_ids", ASCENDING),
                    ("ended_at", DESCENDING),
                    ("_id", DESCENDING),
                ],
                partialFilterExpression={"is_live": False},
                name="ix_calls_participant_ended_at_desc",
            ),
        ]
