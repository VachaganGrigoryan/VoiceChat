from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from typing_extensions import Self

from beanie import Insert, before_event
from pydantic import Field, model_validator
from pymongo import ASCENDING, DESCENDING, IndexModel

from app.db.collections import COL_CONVERSATIONS
from app.db.document import TimestampedDocument
from app.db.models.embedded import ConversationPreviewDocument, OwnerRef
from app.db.object_id import StrId

# The write contract: only these types may be created. ``channel`` re-homes to the
# ``channels`` collection; threads are message topology and no longer exist here.
ConversationType = Literal["dm", "group"]

class ConversationDocument(TimestampedDocument):
    """First-class conversation entity.

    Conversations are DMs or groups. Channels live in their own collection and
    threads are message topology. DMs have no owner; groups carry an owner.
    """

    type: ConversationType = "dm"
    owner: OwnerRef | None = None
    participant_ids: list[StrId] = Field(default_factory=list)
    created_by: StrId
    title: str | None = None
    image: dict[str, Any] | None = None
    encryption: Literal["none", "e2ee"] = "none"
    dm_key: str | None = None
    # Generalized conversation attributes (finalize-messenger-conversation-model).
    # All default so existing dm/group docs remain valid without a rewrite.
    visibility: Literal["private", "public"] = "private"
    posting_policy: Literal["everyone", "admins"] = "everyone"
    space_id: StrId | None = None
    space_visibility: Literal["space_public", "invite_only"] | None = None
    # Public/discoverable + broadcast metadata.
    slug: str | None = None
    description: str | None = None
    member_count: int = Field(default=0, ge=0)
    pinned_message_ids: list[str] = Field(default_factory=list)
    # Generic extension bag (slow-mode, join-approval, history-visibility, ...).
    settings: dict[str, Any] = Field(default_factory=dict)
    last_message_at: datetime | None = None
    last_message_preview: ConversationPreviewDocument | None = None

    @model_validator(mode="after")
    def validate_conversation_invariants(self) -> Self:
        if self.type == "dm":
            if self.owner is not None:
                raise ValueError("DM conversation cannot have an owner")
            if self.space_id is not None:
                raise ValueError("DM conversation cannot have a space_id")
        elif self.type == "group":
            if self.owner is None:
                if self.space_id:
                    self.owner = OwnerRef(type="space", id=self.space_id)
                elif hasattr(self, "created_by") and self.created_by:
                    self.owner = OwnerRef(type="user", id=self.created_by)
            if self.owner is not None and self.owner.type == "space":
                if not self.space_id or str(self.space_id) != str(self.owner.id):
                    raise ValueError("Space-owned group conversation must have matching space_id")
        return self

    @before_event(Insert)
    def _validate_write_invariants(self) -> None:
        if self.type == "dm" and len(set(map(str, self.participant_ids))) != 2:
            raise ValueError("DM conversation must have exactly two distinct participants")

    class Settings:
        name = COL_CONVERSATIONS
        indexes = [
            IndexModel(
                [("dm_key", ASCENDING)],
                unique=True,
                partialFilterExpression={"type": "dm"},
                name="ux_conversations_dm_key",
            ),
            IndexModel(
                [("participant_ids", ASCENDING)],
                name="ix_conversations_participant_ids",
            ),
            IndexModel(
                [("last_message_at", DESCENDING)],
                name="ix_conversations_last_message_at_desc",
            ),
            # Unique slug only among conversations that actually have one
            # (public/discoverable). Private conversations leave slug null.
            IndexModel(
                [("slug", ASCENDING)],
                unique=True,
                partialFilterExpression={"slug": {"$type": "string"}},
                name="ux_conversations_slug",
            ),
            IndexModel(
                [("space_id", ASCENDING)],
                name="ix_conversations_space_id",
            ),
            # Group directory browse. The leading three fields are the listing
            # invariant itself: only space-scoped, space-public groups are
            # discoverable, so a spaceless group never enters the index scan.
            IndexModel(
                [
                    ("type", ASCENDING),
                    ("space_id", ASCENDING),
                    ("space_visibility", ASCENDING),
                    ("created_at", DESCENDING),
                    ("_id", DESCENDING),
                ],
                name="ix_conversations_directory_groups",
            ),
        ]
