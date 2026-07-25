from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import Field
from pymongo import ASCENDING, DESCENDING, IndexModel

from app.db.collections import COL_CONVERSATIONS
from app.db.document import TimestampedDocument
from app.db.models.embedded import ConversationPreviewDocument
from app.db.object_id import StrId


class ConversationDocument(TimestampedDocument):
    """First-class conversation entity.

    Replaces the derived ``sorted("{a}_{b}")`` conversation id. Modeled for N
    participants (``type == "group"``), but the service layer enforces DM-only for
    now. ``dm_key`` reuses the legacy sorted pair string and is uniquely indexed
    (partial, ``type == "dm"``) to guarantee one DM per pair.
    """

    type: Literal["dm", "group", "channel", "thread"] = "dm"
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
