from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

CallType = Literal["audio", "video"]
CallDirection = Literal["incoming", "outgoing"]
CallStatus = Literal[
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


class CallPeerUserSummary(BaseModel):
    id: str
    username: str = ""
    display_name: str | None = None
    avatar: dict | None = None
    is_online: bool = False


class IceServer(BaseModel):
    urls: str | list[str]
    username: str | None = None
    credential: str | None = None


class CallDoc(BaseModel):
    id: str
    caller_user_id: str
    callee_user_id: str
    participant_user_ids: list[str] = Field(min_length=2, max_length=2)
    type: CallType
    status: CallStatus
    room_id: str
    created_at: datetime
    updated_at: datetime
    answered_at: datetime | None = None
    ended_at: datetime | None = None
    expires_at: datetime | None = None
    reconnect_deadline_at: datetime | None = None
    disconnected_user_ids: list[str] = Field(default_factory=list)
    is_live: bool = True


class CallHistoryItem(BaseModel):
    id: str
    peer_user: CallPeerUserSummary
    direction: CallDirection
    type: CallType
    status: Literal["rejected", "cancelled", "expired", "ended"]
    started_at: datetime
    answered_at: datetime | None = None
    ended_at: datetime | None = None
    duration_ms: int = Field(default=0, ge=0)
    message_id: str | None = None


class CreateCallRequest(BaseModel):
    callee_user_id: str = Field(min_length=1)
    type: CallType


class AcceptCallRequest(BaseModel):
    socket_id: str = Field(min_length=1)


class CallSession(BaseModel):
    call: CallDoc
    peer_user: CallPeerUserSummary
    ice_servers: list[IceServer] = Field(default_factory=list)


class CallActionPayload(BaseModel):
    call_id: str = Field(min_length=1)


class CallOfferPayload(CallActionPayload):
    sdp: Any


class CallAnswerPayload(CallActionPayload):
    sdp: Any


class CallIceCandidatePayload(CallActionPayload):
    candidate: Any
