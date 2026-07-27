from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from pydantic import BaseModel, Field

from app.db.object_id import StrId
from app.modules.relationships.schemas import RelationshipView

class SpaceCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=80)
    slug: str = Field(..., min_length=1, max_length=80, pattern=r"^[a-z0-9](?:[a-z0-9-]{1,78}[a-z0-9])$")
    kind: Literal["workspace", "community"] = "workspace"
    visibility: Literal["private", "public"] = "private"
    join_policy: Literal["open", "approval", "invite_only", "closed"] = "open"
    avatar: dict[str, Any] | None = None
    settings: dict[str, Any] = Field(default_factory=dict)

class SpaceView(BaseModel):
    id: str
    name: str
    slug: str
    kind: Literal["workspace", "community"]
    owner_user_id: str
    created_by: str
    avatar: dict[str, Any] | None
    visibility: Literal["private", "public"]
    join_policy: Literal["open", "approval", "invite_only", "closed"]
    settings: dict[str, Any]
    created_at: datetime
    updated_at: datetime
    # Name of the role the viewer holds in this space, or None. The owner is
    # identified by `owner_user_id`, not by a role.
    viewer_role: str | None = None

class SpaceMemberUserSummary(BaseModel):
    id: str
    username: str | None = None
    display_name: str | None = None
    avatar: dict | None = None

class SpaceMemberView(BaseModel):
    id: str
    space_id: str
    user_id: str
    role: str | None = None
    joined_at: datetime
    user: SpaceMemberUserSummary | None = None

class CreateSpaceInviteRequest(BaseModel):
    expires_at: datetime | None = None
    max_uses: int | None = Field(default=None, ge=1)
    approval_required: bool = False
    role_ids: list[str] = Field(default_factory=list)

class SpaceInviteLinkView(BaseModel):
    id: str
    target_type: Literal["space"]
    target_id: str
    code: str
    created_by: str
    expires_at: datetime | None
    max_uses: int | None
    uses: int
    approval_required: bool
    role_ids: list[StrId] = Field(default_factory=list)
    revoked: bool
    invitee_id: str | None = None

class SpaceUserInviteRequest(BaseModel):
    user_id: str

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
    membership: RelationshipView

class SpaceUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    visibility: Literal["private", "public"] | None = None
    settings: dict[str, Any] | None = None

class SpaceChannelView(BaseModel):
    id: str
    title: str | None = None
    description: str | None = None
    space_visibility: Literal["space_public", "invite_only"] | None = None
    joined: bool = False
