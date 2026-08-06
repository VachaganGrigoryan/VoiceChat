from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from typing_extensions import Self

from pydantic import Field, model_validator
from pymongo import ASCENDING, DESCENDING, IndexModel

from app.db.collections import COL_CHANNELS
from app.db.document import TimestampedDocument
from app.db.models.embedded import OwnerRef
from app.db.object_id import StrId


class ChannelDocument(TimestampedDocument):
    """First-class channel entity.

    Persisted in the ``channels`` collection. Owned by either a user or a space via
    ``owner: OwnerRef``. Space-owned channels carry a denormalized ``space_id`` field
    for querying.
    """

    owner: OwnerRef
    space_id: StrId | None = None
    kind: Literal["profile", "text", "announcement"] = "text"
    slug: str
    name: str
    description: str | None = None
    avatar: dict[str, Any] | None = None
    banner: dict[str, Any] | None = None
    visibility: Literal["public", "members", "private"] = "public"
    join_policy: Literal["open", "approval", "invite_only", "closed"] = "open"
    posting_policy: Literal["owner", "moderators", "members", "everyone"] = "everyone"
    comment_policy: Literal["disabled", "followers", "members", "everyone"] = "everyone"
    tags: list[str] = Field(default_factory=list)
    message_count: int = Field(default=0, ge=0)
    follower_count: int = Field(default=0, ge=0)
    # Pinning was previously refused for channels only for want of somewhere to
    # record it; a channel tracks its pinned set exactly as a conversation does.
    pinned_message_ids: list[str] = Field(default_factory=list)
    last_message_id: str | None = None
    last_activity_at: datetime | None = None
    legacy_conversation_id: str | None = None
    created_by: StrId

    @model_validator(mode="after")
    def validate_channel_invariants(self) -> Self:
        if self.kind == "profile" and self.owner.type != "user":
            raise ValueError("Profile channel must be owned by a user")
        if self.owner.type == "space":
            if not self.space_id:
                raise ValueError("Space-owned channel must have space_id set")
            if str(self.space_id) != str(self.owner.id):
                raise ValueError("space_id must match owner.id for space-owned channels")
        elif self.owner.type == "user":
            if self.kind == "profile" and self.space_id is not None:
                raise ValueError("User profile channel cannot have space_id")
        return self

    class Settings:
        name = COL_CHANNELS
        indexes = [
            IndexModel(
                [("owner.type", ASCENDING), ("owner.id", ASCENDING)],
                name="ix_channels_owner",
            ),
            IndexModel(
                [("space_id", ASCENDING)],
                name="ix_channels_space_id",
            ),
            IndexModel(
                [("owner.type", ASCENDING), ("owner.id", ASCENDING), ("slug", ASCENDING)],
                unique=True,
                name="ux_channels_owner_slug",
            ),
            IndexModel(
                [("visibility", ASCENDING), ("kind", ASCENDING)],
                name="ix_channels_visibility_kind",
            ),
            IndexModel(
                [("tags", ASCENDING)],
                name="ix_channels_tags",
            ),
            # Directory browse. Without these the directory is a collection scan
            # on its hottest path. `_id` trails the sort key because the cursor
            # is a keyset on `(sort_value, _id)`, never an offset.
            IndexModel(
                [
                    ("visibility", ASCENDING),
                    ("kind", ASCENDING),
                    ("follower_count", DESCENDING),
                    ("_id", DESCENDING),
                ],
                name="ix_channels_directory_popular",
            ),
            IndexModel(
                [
                    ("visibility", ASCENDING),
                    ("kind", ASCENDING),
                    ("last_activity_at", DESCENDING),
                    ("_id", DESCENDING),
                ],
                name="ix_channels_directory_recent",
            ),
        ]
