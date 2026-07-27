from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field
from pymongo import ASCENDING, IndexModel

from app.db.collections import COL_INVITE_LINKS
from app.db.document import TimestampedDocument
from app.db.object_id import StrId


class InviteLinkDocument(TimestampedDocument):
    """A redeemable invite that creates or activates a membership relationship."""

    target_type: Literal["conversation", "channel", "space"]
    target_id: StrId
    code: str
    created_by: StrId
    role_ids: list[StrId] = Field(default_factory=list)
    expires_at: datetime | None = None
    invitee_id: StrId | None = None
    max_uses: int | None = Field(default=None, ge=1)
    uses: int = Field(default=0, ge=0)
    approval_required: bool = False
    revoked: bool = False

    class Settings:
        name = COL_INVITE_LINKS
        indexes = [
            IndexModel([("code", ASCENDING)], unique=True, name="ux_invite_links_code"),
            IndexModel(
                [("target_type", ASCENDING), ("target_id", ASCENDING)],
                name="ix_invite_links_target",
            ),
        ]
