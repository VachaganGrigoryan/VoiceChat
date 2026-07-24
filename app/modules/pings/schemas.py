from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.db.object_id import StrId

PingStatus = Literal["pending", "accepted", "declined", "cancelled", "expired", "blocked"]
PingStatusView = Literal[
    "none", "incoming_pending", "outgoing_pending", "accepted", "declined", "blocked"
]


class SendPingRequest(BaseModel):
    to_user_id: str = Field(min_length=1)


class PeerActionRequest(BaseModel):
    peer_user_id: str


class PingResponse(BaseModel):
    id: StrId
    from_user_id: StrId
    to_user_id: StrId
    status: PingStatus
    created_at: datetime
    updated_at: datetime
    responded_at: datetime | None = None


class PeerUserSummary(BaseModel):
    id: StrId
    username: str
    display_name: str | None = None
    avatar: dict | None = None
    is_online: bool = False


class PingListItem(BaseModel):
    ping: PingResponse
    peer: PeerUserSummary


class ContactState(BaseModel):
    can_ping: bool
    chat_allowed: bool
    ping_status: PingStatusView
    blocked_by_me: bool = False
    blocks_me: bool = False


class ContactListItem(BaseModel):
    ping: PingResponse
    peer: PeerUserSummary
    conversation_id: StrId | None = None


class SharedConversationSummary(BaseModel):
    id: StrId
    type: str
    title: str | None = None


class SharedSpaceSummary(BaseModel):
    id: StrId
    name: str
    slug: str


class ContactExtras(BaseModel):
    """Extra contact data folded into the `/users/{id}` response on demand."""

    connection_timestamp: datetime | None = None
    conversation_id: StrId | None = None
    shared_conversations: list[SharedConversationSummary] = Field(default_factory=list)
    shared_spaces: list[SharedSpaceSummary] = Field(default_factory=list)
