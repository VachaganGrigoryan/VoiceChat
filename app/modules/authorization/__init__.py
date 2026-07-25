"""Central authorization: roles, ownership, and the one decision point.

Every authorization question goes through `AuthorizationService.can` (§56); no
module should re-derive access from a role name. `OwnershipResolver` answers the
owner-bypass question, `RoleService` manages the `roles` collection, and
`permissions` holds the string vocabulary those roles are built from.
"""

from __future__ import annotations

from app.modules.authorization.ownership import OwnershipResolver
from app.modules.authorization.permissions import PERMISSIONS
from app.modules.authorization.repository import RolesRepository
from app.modules.authorization.router import roles_router
from app.modules.authorization.roles import RoleService
from app.modules.authorization.service import AuthorizationService, ResourceType

__all__ = [
    "AuthorizationService",
    "OwnershipResolver",
    "PERMISSIONS",
    "ResourceType",
    "RoleService",
    "RolesRepository",
    "roles_router",
]
