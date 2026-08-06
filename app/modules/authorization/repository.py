from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from app.core.errors import AppError
from app.db.models import RoleDocument
from app.db.models.role import RoleScopeType
from app.db.object_id import parse_object_id as _oid
from app.db.repository import BaseRepository


# Role *names* are stable — system roles never rename and custom ones rarely do
# — but they are read on every membership projection. Cache id -> name for the
# process and invalidate on the two writes that can change it.
_NAME_CACHE: dict[str, str] = {}


class RolesRepository(BaseRepository[RoleDocument]):
    model = RoleDocument

    async def create(
        self,
        *,
        scope_type: RoleScopeType,
        scope_id: str,
        name: str,
        permissions: list[str],
        priority: int = 0,
        system: bool = False,
        created_by: str | None = None,
    ) -> RoleDocument:
        now = datetime.now(UTC)
        doc = RoleDocument(
            scope_type=scope_type,
            scope_id=str(scope_id),
            name=name,
            permissions=list(permissions),
            priority=priority,
            system=system,
            created_by=str(created_by) if created_by else None,
            created_at=now,
            updated_at=now,
        )
        try:
            await doc.insert()
        except DuplicateKeyError as exc:
            raise AppError(
                code="ROLE_NAME_TAKEN",
                message=f"A role named {name!r} already exists in this scope",
                status_code=409,
            ) from exc
        return doc

    async def list_for_scope(
        self, *, scope_type: RoleScopeType, scope_id: str
    ) -> list[RoleDocument]:
        return (
            await RoleDocument.find(
                {"scope_type": scope_type, "scope_id": str(scope_id)}
            )
            .sort("-priority", "name")
            .to_list()
        )

    async def find_by_name(
        self, *, scope_type: RoleScopeType, scope_id: str, name: str
    ) -> RoleDocument | None:
        return await RoleDocument.find_one(
            {"scope_type": scope_type, "scope_id": str(scope_id), "name": name}
        )

    async def list_by_ids(self, role_ids: list[str]) -> list[RoleDocument]:
        """Load roles by id, silently dropping ids that aren't valid ObjectIds.

        Legacy `role_ids` still hold role *names* ("admin", "member") until the
        backfill runs, so a malformed id is expected rather than exceptional.
        """
        object_ids = []
        for role_id in role_ids:
            try:
                object_ids.append(_oid(str(role_id)))
            except AppError:
                continue
        if not object_ids:
            return []
        return await RoleDocument.find({"_id": {"$in": object_ids}}).to_list()

    async def names_by_id(self, role_ids: list[str]) -> dict[str, str]:
        """Role id -> name for the ids given, served from the process cache."""
        wanted = [str(role_id) for role_id in role_ids]
        missing = [role_id for role_id in wanted if role_id not in _NAME_CACHE]
        if missing:
            for role in await self.list_by_ids(missing):
                _NAME_CACHE[role.str_id] = role.name
        return {
            role_id: _NAME_CACHE[role_id]
            for role_id in wanted
            if role_id in _NAME_CACHE
        }

    async def update(
        self, *, role_id: str, updates: dict[str, Any]
    ) -> RoleDocument | None:
        if not updates:
            return await self.get_by_id(role_id)
        _NAME_CACHE.pop(str(role_id), None)
        set_fields = {**updates, "updated_at": datetime.now(UTC)}
        try:
            return await self.find_one_and_update(
                {"_id": _oid(role_id)}, {"$set": set_fields}
            )
        except DuplicateKeyError as exc:
            raise AppError(
                code="ROLE_NAME_TAKEN",
                message="A role with this name already exists in this scope",
                status_code=409,
            ) from exc

    async def upsert_system_role(
        self,
        *,
        scope_type: RoleScopeType,
        scope_id: str,
        name: str,
        permissions: list[str],
        priority: int,
    ) -> RoleDocument:
        """Idempotently ensure a system role exists, leaving custom edits intact.

        Seeding runs on every resource create and again during migration, so it
        must not clobber permissions an operator has since tuned: only the
        insert path writes ``permissions``.
        """
        now = datetime.now(UTC)
        raw = await self.raw.find_one_and_update(
            {"scope_type": scope_type, "scope_id": str(scope_id), "name": name},
            {
                "$set": {"updated_at": now},
                "$setOnInsert": {
                    "scope_type": scope_type,
                    "scope_id": str(scope_id),
                    "name": name,
                    "permissions": list(permissions),
                    "priority": priority,
                    "system": True,
                    "created_by": None,
                    "created_at": now,
                },
            },
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
        return RoleDocument.model_validate(raw)

    async def delete_for_scope(
        self, *, scope_type: RoleScopeType, scope_id: str
    ) -> int:
        roles = await self.list_for_scope(scope_type=scope_type, scope_id=scope_id)
        for role in roles:
            _NAME_CACHE.pop(role.str_id, None)
        result = await self.raw.delete_many(
            {"scope_type": scope_type, "scope_id": str(scope_id)}
        )
        return int(result.deleted_count)

    def forget_cached_name(self, role_id: str) -> None:
        _NAME_CACHE.pop(str(role_id), None)
