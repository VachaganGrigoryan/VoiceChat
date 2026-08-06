from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.db.models import RelationshipDocument
from app.db.models.relationship import (
    RelationshipInitiation,
    RelationshipKind,
    RelationshipStatus,
    RelationshipTargetType,
)
from app.db.object_id import StrId


class RelationshipStateView(BaseModel):
    muted_until: datetime | None = None
    archived: bool = False
    pinned: bool = False
    hidden: bool = False
    folder: str | None = None
    last_read_message_id: str | None = None
    notification_level: Literal["all", "mentions", "none"] = "all"


class RelationshipView(BaseModel):
    id: StrId
    kind: RelationshipKind
    user_id: StrId
    target_type: RelationshipTargetType
    target_id: StrId
    status: RelationshipStatus
    initiation: RelationshipInitiation
    initiated_by: StrId
    approved_by: StrId | None = None
    pair_id: str | None = None
    role_ids: list[StrId] = Field(default_factory=list)
    state: RelationshipStateView = Field(default_factory=RelationshipStateView)
    requested_at: datetime
    activated_at: datetime | None = None
    ended_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


ConnectionDirection = Literal["incoming", "outgoing"]
ConnectionStatusView = Literal[
    "none", "pending", "active", "declined", "revoked", "blocked"
]


class PeerUserSummary(BaseModel):
    id: StrId
    username: str
    display_name: str | None = None
    avatar: dict | None = None
    is_online: bool = False


class ConnectionListItem(BaseModel):
    relationship: RelationshipView
    peer: PeerUserSummary
    direction: ConnectionDirection
    conversation_id: StrId | None = None


class ConnectionState(BaseModel):
    can_ping: bool
    chat_allowed: bool
    connection_status: ConnectionStatusView
    direction: ConnectionDirection | None = None
    relationship_id: StrId | None = None
    blocked_by_me: bool = False
    blocks_me: bool = False


class SharedConversationSummary(BaseModel):
    id: StrId
    type: str
    title: str | None = None


class SharedSpaceSummary(BaseModel):
    id: StrId
    name: str
    slug: str


class ConnectionExtras(BaseModel):
    connection_timestamp: datetime | None = None
    conversation_id: StrId | None = None
    shared_conversations: list[SharedConversationSummary] = Field(default_factory=list)
    shared_spaces: list[SharedSpaceSummary] = Field(default_factory=list)


def to_relationship_view(doc: RelationshipDocument) -> RelationshipView:
    return RelationshipView(
        id=doc.str_id,
        kind=doc.kind,
        user_id=str(doc.user_id),
        target_type=doc.target_type,
        target_id=str(doc.target_id),
        status=doc.status,
        initiation=doc.initiation,
        initiated_by=str(doc.initiated_by),
        approved_by=str(doc.approved_by) if doc.approved_by else None,
        pair_id=doc.pair_id,
        role_ids=[str(role_id) for role_id in doc.role_ids],
        state=RelationshipStateView.model_validate(
            doc.state.model_dump(), from_attributes=True
        ),
        requested_at=doc.requested_at,
        activated_at=doc.activated_at,
        ended_at=doc.ended_at,
        created_at=doc.created_at,
        updated_at=doc.updated_at,
    )
