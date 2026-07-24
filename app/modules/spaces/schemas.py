from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from pydantic import BaseModel, Field

class SpaceCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=80)
    slug: str = Field(..., min_length=1, max_length=80, pattern=r"^[a-z0-9](?:[a-z0-9-]{1,78}[a-z0-9])$")
    kind: Literal["workspace", "community"] = "workspace"
    visibility: Literal["private", "public"] = "private"
    avatar: dict[str, Any] | None = None
    settings: dict[str, Any] = Field(default_factory=dict)

class SpaceView(BaseModel):
    id: str
    name: str
    slug: str
    kind: Literal["workspace", "community"]
    created_by: str
    avatar: dict[str, Any] | None
    visibility: Literal["private", "public"]
    settings: dict[str, Any]
    created_at: datetime
    updated_at: datetime

class SpaceMemberView(BaseModel):
    id: str
    space_id: str
    user_id: str
    role: Literal["owner", "admin", "member"]
    joined_at: datetime

class CreateSpaceInviteRequest(BaseModel):
    expires_at: datetime | None = None
    max_uses: int | None = Field(default=None, ge=1)
    requires_approval: bool = False

class SpaceInviteLinkView(BaseModel):
    id: str
    target_type: Literal["space"]
    target_id: str
    code: str
    created_by: str
    expires_at: datetime | None
    max_uses: int | None
    use_count: int
    requires_approval: bool
    revoked: bool

class SpaceJoinRequestView(BaseModel):
    id: str
    target_type: Literal["space"]
    target_id: str
    user_id: str
    status: Literal["pending", "approved", "rejected"]
    invite_code: str | None
    created_at: datetime
    responded_at: datetime | None

class RedeemSpaceInviteResponse(BaseModel):
    status: Literal["joined", "pending"]
    space: SpaceView | None = None
    join_request: SpaceJoinRequestView | None = None
