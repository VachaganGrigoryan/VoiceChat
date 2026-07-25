from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.db.models import RoleDocument


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
