from __future__ import annotations

from app.core.errors import AppError
from app.db.models import RoleDocument
from app.db.models.role import RoleScopeType
from app.modules.authorization.permissions import (
    CALL_CREATE,
    CALL_MANAGE,
    CHANNEL_CREATE,
    CHANNEL_DELETE,
    CHANNEL_MANAGE,
    GROUP_CREATE,
    GROUP_MANAGE,
    MEMBER_APPROVE,
    MEMBER_INVITE,
    MEMBER_REMOVE,
    MEMBER_VIEW,
    MESSAGE_CREATE,
    MESSAGE_DELETE_ANY,
    MESSAGE_DELETE_OWN,
    MESSAGE_EDIT_OWN,
    MESSAGE_PIN,
    MESSAGE_READ,
    PERMISSIONS,
    POLL_CREATE,
    POLL_MANAGE,
    REACTION_CREATE,
    REACTION_DELETE_ANY,
    REACTION_DELETE_OWN,
    RESOURCE_MANAGE,
    RESOURCE_VIEW,
    ROLE_VIEW,
    THREAD_CREATE,
    THREAD_REPLY,
    unknown_permissions,
)
from app.modules.authorization.repository import RolesRepository

ROLE_ADMIN = "Admin"
ROLE_MODERATOR = "Moderator"
ROLE_MEMBER = "Member"
ROLE_GUEST = "Guest"

# Seeded per resource. Owner is deliberately absent — it is a bypass resolved
# from the resource's owner, not a role (§49, §51). Permissions that don't apply
# to a given scope (e.g. `channel.create` on a conversation) are inert, so one
# table serves all three scopes.
DEFAULT_SYSTEM_ROLES: tuple[tuple[str, int, frozenset[str]], ...] = (
    (ROLE_ADMIN, 300, PERMISSIONS),
    (
        ROLE_MODERATOR,
        200,
        frozenset(
            {
                RESOURCE_VIEW,
                RESOURCE_MANAGE,
                MEMBER_VIEW,
                MEMBER_INVITE,
                MEMBER_APPROVE,
                MEMBER_REMOVE,
                ROLE_VIEW,
                MESSAGE_READ,
                MESSAGE_CREATE,
                MESSAGE_EDIT_OWN,
                MESSAGE_DELETE_OWN,
                MESSAGE_DELETE_ANY,
                MESSAGE_PIN,
                THREAD_CREATE,
                THREAD_REPLY,
                REACTION_CREATE,
                REACTION_DELETE_OWN,
                REACTION_DELETE_ANY,
                CALL_CREATE,
                CALL_MANAGE,
                POLL_CREATE,
                POLL_MANAGE,
            }
        ),
    ),
    (
        ROLE_MEMBER,
        100,
        frozenset(
            {
                RESOURCE_VIEW,
                MEMBER_VIEW,
                ROLE_VIEW,
                MESSAGE_READ,
                MESSAGE_CREATE,
                MESSAGE_EDIT_OWN,
                MESSAGE_DELETE_OWN,
                THREAD_CREATE,
                THREAD_REPLY,
                REACTION_CREATE,
                REACTION_DELETE_OWN,
                CALL_CREATE,
                POLL_CREATE,
            }
        ),
    ),
    (
        ROLE_GUEST,
        0,
        frozenset({RESOURCE_VIEW, MEMBER_VIEW, MESSAGE_READ}),
    ),
)

# Legacy participant/space-member role names -> seeded role name. ``owner`` maps
# to nothing: ownership is a bypass, so an owner needs no role document.
LEGACY_ROLE_NAMES: dict[str, str | None] = {
    "owner": None,
    "admin": ROLE_ADMIN,
    "moderator": ROLE_MODERATOR,
    "member": ROLE_MEMBER,
    "guest": ROLE_GUEST,
    # A channel subscriber is feed interest (a follow), not a membership role.
    "subscriber": None,
}

SPACE_ONLY_PERMISSIONS: frozenset[str] = frozenset(
    {CHANNEL_CREATE, CHANNEL_MANAGE, CHANNEL_DELETE, GROUP_CREATE, GROUP_MANAGE}
)


class RoleService:
    """CRUD over the `roles` collection plus per-resource system-role seeding."""

    def __init__(self, repo: RolesRepository | None = None) -> None:
        self.repo = repo or RolesRepository()

    async def create_role(
        self,
        *,
        scope_type: RoleScopeType,
        scope_id: str,
        name: str,
        permissions: list[str],
        priority: int = 0,
        created_by: str | None = None,
    ) -> RoleDocument:
        clean_name = name.strip()
        if not clean_name:
            raise AppError(
                code="ROLE_INVALID_NAME",
                message="Role name is required",
                status_code=400,
            )
        self._validate_permissions(permissions)
        return await self.repo.create(
            scope_type=scope_type,
            scope_id=scope_id,
            name=clean_name,
            permissions=sorted(set(permissions)),
            priority=priority,
            system=False,
            created_by=created_by,
        )

    async def list_roles(
        self, *, scope_type: RoleScopeType, scope_id: str
    ) -> list[RoleDocument]:
        return await self.repo.list_for_scope(scope_type=scope_type, scope_id=scope_id)

    async def get_role(self, role_id: str) -> RoleDocument:
        return await self.repo.get_or_404(
            role_id, code="ROLE_NOT_FOUND", message="Role not found"
        )

    async def update_role(
        self,
        *,
        role_id: str,
        name: str | None = None,
        permissions: list[str] | None = None,
        priority: int | None = None,
    ) -> RoleDocument:
        role = await self.get_role(role_id)
        updates: dict[str, object] = {}
        if name is not None:
            clean_name = name.strip()
            if not clean_name:
                raise AppError(
                    code="ROLE_INVALID_NAME",
                    message="Role name is required",
                    status_code=400,
                )
            if role.system and clean_name != role.name:
                raise AppError(
                    code="ROLE_SYSTEM_IMMUTABLE",
                    message="A system role cannot be renamed",
                    status_code=400,
                )
            updates["name"] = clean_name
        if permissions is not None:
            self._validate_permissions(permissions)
            updates["permissions"] = sorted(set(permissions))
        if priority is not None:
            updates["priority"] = priority
        updated = await self.repo.update(role_id=role.str_id, updates=updates)
        if updated is None:
            raise AppError(
                code="ROLE_NOT_FOUND", message="Role not found", status_code=404
            )
        return updated

    async def delete_role(self, role_id: str) -> None:
        role = await self.get_role(role_id)
        if role.system:
            raise AppError(
                code="ROLE_SYSTEM_IMMUTABLE",
                message="A system role cannot be deleted",
                status_code=400,
            )
        await role.delete()

    async def seed_default_roles(
        self, *, scope_type: RoleScopeType, scope_id: str
    ) -> dict[str, RoleDocument]:
        """Ensure the four system roles exist for a resource, keyed by name.

        Idempotent: existing roles keep whatever permissions they currently
        carry, so re-running after an operator edits a role is safe.
        """
        seeded: dict[str, RoleDocument] = {}
        for name, priority, permissions in DEFAULT_SYSTEM_ROLES:
            granted = set(permissions)
            if scope_type != "space":
                # Creating/managing channels and groups is a space-scoped power.
                granted -= SPACE_ONLY_PERMISSIONS
            seeded[name] = await self.repo.upsert_system_role(
                scope_type=scope_type,
                scope_id=scope_id,
                name=name,
                permissions=sorted(granted),
                priority=priority,
            )
        return seeded

    async def resolve_role_id(
        self, *, scope_type: RoleScopeType, scope_id: str, name: str
    ) -> str:
        """The id of the named role in this scope, seeding system roles if absent.

        Membership writes name a role ("Member", "Admin") rather than an id, so
        this is the single place that turns one into the other. Resources that
        predate seeding get their system roles on first assignment.
        """
        role = await self.repo.find_by_name(
            scope_type=scope_type, scope_id=scope_id, name=name
        )
        if role is not None:
            return role.str_id
        seeded = await self.seed_default_roles(
            scope_type=scope_type, scope_id=scope_id
        )
        if name in seeded:
            return seeded[name].str_id
        raise AppError(
            code="ROLE_NOT_FOUND",
            message=f"No role named {name!r} in this scope",
            status_code=404,
        )

    async def role_id_for_legacy_name(
        self, *, scope_type: RoleScopeType, scope_id: str, legacy_role: str
    ) -> str | None:
        """The seeded role id a legacy role name maps to, or None for bypass roles."""
        target = LEGACY_ROLE_NAMES.get(legacy_role.lower(), ROLE_MEMBER)
        if target is None:
            return None
        role = await self.repo.find_by_name(
            scope_type=scope_type, scope_id=scope_id, name=target
        )
        return role.str_id if role is not None else None

    @staticmethod
    def _validate_permissions(permissions: list[str]) -> None:
        unknown = unknown_permissions(permissions)
        if unknown:
            raise AppError(
                code="ROLE_UNKNOWN_PERMISSIONS",
                message=f"Unknown permissions: {', '.join(sorted(unknown))}",
                status_code=400,
            )
