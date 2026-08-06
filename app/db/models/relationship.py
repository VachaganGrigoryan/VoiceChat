from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import Field
from pymongo import ASCENDING, DESCENDING, IndexModel

from app.db.collections import COL_RELATIONSHIPS
from app.db.document import EmbeddedBase, TimestampedDocument
from app.db.object_id import StrId

RelationshipKind = Literal["connection", "follow", "membership"]
RelationshipTargetType = Literal["user", "conversation", "channel", "space"]
RelationshipStatus = Literal["pending", "active", "declined", "revoked"]
RelationshipInitiation = Literal["request", "invite", "direct", "system"]


class RelationshipPermissionOverrides(EmbeddedBase):
    """Per-membership grants/denials layered over the roles in ``role_ids``."""

    allow: list[str] = Field(default_factory=list)
    deny: list[str] = Field(default_factory=list)


class RelationshipState(EmbeddedBase):
    """Per-user resource state, replacing the former participant inbox fields."""

    muted_until: datetime | None = None
    archived: bool = False
    pinned: bool = False
    hidden: bool = False
    folder: str | None = None
    draft_text: str | None = None
    draft_updated_at: datetime | None = None
    last_read_at: datetime | None = None
    last_read_message_id: str | None = None
    notification_level: Literal["all", "mentions", "none"] = "all"


class RelationshipDocument(TimestampedDocument):
    """One edge between a user and a user or resource (RefactoringPlan §5).

    A single collection backs all three kinds — ``connection`` (mutual user↔user),
    ``follow`` (one-directional feed interest), and ``membership`` (user↔resource
    participation) — so one lifecycle engine and one index set serve them all.
    Validation is per-kind rather than per-collection; see §93 invariants.
    """

    kind: RelationshipKind
    user_id: StrId

    target_type: RelationshipTargetType
    target_id: StrId

    status: RelationshipStatus = "pending"
    initiation: RelationshipInitiation = "request"
    initiated_by: StrId
    approved_by: StrId | None = None

    requested_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    activated_at: datetime | None = None
    ended_at: datetime | None = None

    # connection only: canonical `{min}_{max}` user pair, unique per pair.
    pair_id: str | None = None

    # membership only.
    role_ids: list[StrId] = Field(default_factory=list)
    permission_overrides: RelationshipPermissionOverrides | None = None

    state: RelationshipState = Field(default_factory=RelationshipState)

    class Settings:
        name = COL_RELATIONSHIPS
        indexes = [
            IndexModel(
                [("kind", ASCENDING), ("user_id", ASCENDING), ("status", ASCENDING)],
                name="ix_relationships_kind_user_status",
            ),
            IndexModel(
                [
                    ("kind", ASCENDING),
                    ("target_type", ASCENDING),
                    ("target_id", ASCENDING),
                    ("status", ASCENDING),
                ],
                name="ix_relationships_kind_target_status",
            ),
            IndexModel(
                [("user_id", ASCENDING), ("updated_at", DESCENDING)],
                name="ix_relationships_user_updatedAt_desc",
            ),
            # Partial uniqueness per kind (§98): follow/membership are unique per
            # (user, target); connection is unique per canonical pair.
            IndexModel(
                [
                    ("kind", ASCENDING),
                    ("user_id", ASCENDING),
                    ("target_type", ASCENDING),
                    ("target_id", ASCENDING),
                ],
                unique=True,
                partialFilterExpression={"kind": {"$in": ["follow", "membership"]}},
                name="ux_relationships_user_target",
            ),
            IndexModel(
                [("kind", ASCENDING), ("pair_id", ASCENDING)],
                unique=True,
                partialFilterExpression={"kind": "connection"},
                name="ux_relationships_connection_pair",
            ),
        ]
