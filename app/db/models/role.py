from __future__ import annotations

from typing import Literal

from pydantic import Field
from pymongo import ASCENDING, IndexModel

from app.db.collections import COL_ROLES
from app.db.document import TimestampedDocument
from app.db.object_id import StrId

RoleScopeType = Literal["space", "conversation", "channel"]


class RoleDocument(TimestampedDocument):
    """A named permission bundle scoped to one resource (RefactoringPlan §49).

    Roles are attached to *active* memberships through ``Relationship.role_ids``;
    they never carry ownership. Owner is a bypass resolved from the resource's
    ``OwnerRef``, not a role — so no ``Owner`` role document is ever seeded.
    Permissions are strings from ``app.modules.authorization.permissions``; there
    are deliberately no boolean permission fields (§52).
    """

    scope_type: RoleScopeType
    scope_id: StrId
    name: str
    permissions: list[str] = Field(default_factory=list)
    # Higher wins for display/ordering. Resolution unions permissions across a
    # user's roles and applies deny-precedence, so priority never gates a grant.
    priority: int = 0
    system: bool = False
    created_by: StrId | None = None

    class Settings:
        name = COL_ROLES
        indexes = [
            IndexModel(
                [("scope_type", ASCENDING), ("scope_id", ASCENDING)],
                name="ix_roles_scope",
            ),
            IndexModel(
                [
                    ("scope_type", ASCENDING),
                    ("scope_id", ASCENDING),
                    ("name", ASCENDING),
                ],
                unique=True,
                name="ux_roles_scope_name",
            ),
        ]
