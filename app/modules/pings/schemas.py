from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

PingStatus = Literal["pending", "accepted", "declined", "cancelled", "expired", "blocked"]
PingStatusView = Literal["none", "incoming_pending", "outgoing_pending", "accepted", "declined"]


class SendPingRequest(BaseModel):
    to_user_id: str = Field(min_length=1)


class PeerActionRequest(BaseModel):
    peer_user_id: str


class PingResponse(BaseModel):
    id: str
    from_user_id: str
    to_user_id: str
    status: PingStatus
    created_at: datetime
    updated_at: datetime
    responded_at: datetime | None = None


class PeerUserSummary(BaseModel):
    id: str
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