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

# The write contract: only these types may be created. ``channel`` and ``thread``
# re-home to the ``channels`` collection and to message topology respectively.
ConversationType = Literal["dm", "group"]

# Legacy discriminator values that still exist on disk. Kept in the field's Literal so
# reads of un-migrated rows validate; rejected on insert by ``_reject_legacy_types``.
LEGACY_CONVERSATION_TYPES: frozenset[str] = frozenset({"channel", "thread"})


class ConversationDocument(TimestampedDocument):
    """First-class conversation entity.

    The write contract is ``ConversationType`` (``dm`` or ``group``). The persisted field
    still accepts the legacy ``channel``/``thread`` values so un-migrated rows remain
    readable until ``unified-messages`` and ``channels-and-profile-feed`` re-home them;
    creating one is rejected. DMs have no owner; Groups carry an owner (User or Space
    via OwnerRef).
    """

    type: Literal["dm", "group", "channel", "thread"] = "dm"
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
    # Who may read this channel's feed of posts. Independent of ``visibility``
    # (which governs slug-based discoverability): ``members`` keeps today's
    # membership-gated behavior; ``contacts`` opens reads to the owner's accepted
    # contacts; ``public`` to any authenticated user. Only meaningful for channels.
    read_policy: Literal["members", "contacts", "public"] = "members"
    space_id: StrId | None = None
    space_visibility: Literal["space_public", "invite_only"] | None = None
    # Thread-as-sub-conversation linkage (type == "thread").
    parent_conversation_id: StrId | None = None
    root_message_id: str | None = None
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
    def _reject_legacy_types(self) -> None:
        """Block new ``channel``/``thread`` conversations at the persistence boundary.

        Placed on insert rather than in the field's Literal so reads of un-migrated rows
        keep validating, and so the guard covers every write path (repository included),
        not just the conversation service.
        """
        if self.type in LEGACY_CONVERSATION_TYPES:
            raise ValueError(
                f"Cannot create a conversation of type {self.type!r}: "
                "channels live in the channels collection and threads are message topology"
            )
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
            IndexModel(
                [("parent_conversation_id", ASCENDING)],
                name="ix_conversations_parent_conversation_id",
            ),
        ]
