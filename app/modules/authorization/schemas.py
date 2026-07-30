from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.db.models import RoleDocument
from app.modules.authorization.capabilities import (
    MAX_RESOURCES_PER_REQUEST,
    ResourceCapabilities,
)


class RoleView(BaseModel):
    id: str
    scope_type: str
    scope_id: str
    name: str
    permissions: list[str]
    priority: int
    system: bool
    created_at: datetime
    updated_at: datetime


class CreateRoleRequest(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    permissions: list[str] = Field(default_factory=list, max_length=64)
    priority: int = 0


class UpdateRoleRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    permissions: list[str] | None = Field(default=None, max_length=64)
    priority: int | None = None


class AssignRolesRequest(BaseModel):
    """Replace a membership's roles (§89). Ids must be roles in that scope."""

    role_ids: list[str] = Field(default_factory=list, max_length=16)


class ResourceRef(BaseModel):
    type: Literal["space", "conversation", "channel"]
    id: str


class ViewerStandingView(BaseModel):
    """Posture, not permission. See `capabilities.ResourceStanding`."""

    is_owner: bool
    membership_status: str | None = None
    is_follower: bool = False
    role_ids: list[str] = Field(default_factory=list)


class ResourceCapabilitiesView(BaseModel):
    resource: ResourceRef
    allowed: list[str]
    #: Explicit rather than "absent means denied": as the action vocabulary
    #: grows, an older client must be able to tell "refused" from
    #: "not evaluated".
    denied: list[str]
    standing: ViewerStandingView
    #: The resource's policy fields, for management forms to render and edit.
    #: Never an authorization decision — gate on `allowed`.
    policy: dict[str, Any] = Field(default_factory=dict)


class CapabilitiesRequest(BaseModel):
    """Ask about many resources at once.

    Actions ending in `.own` are evaluated with the caller as author, so a
    granted `message.edit.own` means "you may edit messages you wrote". The
    client must still compare `message.sender_id` before offering the action on
    a specific item.
    """

    resources: list[ResourceRef] = Field(
        min_length=1, max_length=MAX_RESOURCES_PER_REQUEST
    )
    actions: list[str] | None = Field(default=None, max_length=64)


class CapabilitiesResponse(BaseModel):
    capabilities: list[ResourceCapabilitiesView]


def to_capabilities_view(result: ResourceCapabilities) -> ResourceCapabilitiesView:
    return ResourceCapabilitiesView(
        resource=ResourceRef(type=result.resource_type, id=result.resource_id),
        allowed=result.allowed,
        denied=result.denied,
        standing=ViewerStandingView(
            is_owner=result.standing.is_owner,
            membership_status=result.standing.membership_status,
            is_follower=result.standing.is_follower,
            role_ids=result.standing.role_ids,
        ),
        policy=result.policy,
    )


def to_role_view(role: RoleDocument) -> RoleView:
    return RoleView(
        id=role.str_id,
        scope_type=role.scope_type,
        scope_id=str(role.scope_id),
        name=role.name,
        permissions=list(role.permissions),
        priority=role.priority,
        system=role.system,
        created_at=role.created_at,
        updated_at=role.updated_at,
    )
