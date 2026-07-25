"""Roles APIs (RefactoringPlan §89).

Roles are scoped to a resource, so they are listed and created under that
resource. Assignment lives on the membership, because a role only means
anything through an active membership (§50).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from starlette.requests import Request

from app.core.errors import AppError
from app.core.errors.openapi import build_error_responses
from app.core.http import SuccessResponse, ok
from app.core.security import get_current_user_id
from app.db.models.role import RoleScopeType
from app.modules.authorization.permissions import ROLE_MANAGE, ROLE_VIEW
from app.modules.authorization.repository import RolesRepository
from app.modules.authorization.roles import RoleService
from app.modules.authorization.schemas import (
    AssignRolesRequest,
    CreateRoleRequest,
    RoleView,
    UpdateRoleRequest,
    to_role_view,
)
from app.modules.authorization.service import AuthorizationService
from app.modules.relationships.repository import RelationshipsRepository
from app.modules.relationships.schemas import RelationshipView, to_relationship_view

_RESPONSES = build_error_responses(400, 401, 403, 404, 409, 422, 500)

roles_router = APIRouter(tags=["roles"], responses=_RESPONSES)

# URL segment -> the scope it addresses. The same four handlers are mounted once
# per segment (§89), so only these three prefixes exist as routes.
_SCOPES: dict[str, RoleScopeType] = {
    "spaces": "space",
    "conversations": "conversation",
    "channels": "channel",
}


def get_role_service() -> RoleService:
    return RoleService()


def get_authorization_service() -> AuthorizationService:
    return AuthorizationService()


async def _require_role_in_scope(
    service: RoleService, *, role_id: str, scope: RoleScopeType, resource_id: str
) -> None:
    role = await service.get_role(role_id)
    if role.scope_type != scope or str(role.scope_id) != str(resource_id):
        raise AppError(
            code="ROLE_NOT_IN_SCOPE",
            message="Role does not belong to this resource",
            status_code=400,
        )


def _mount(segment: str, scope: RoleScopeType) -> None:
    """Bind the role endpoints for one resource scope.

    Built as closures over ``scope`` so the scope is fixed by the route rather
    than read from the path — an unknown scope cannot be requested at all.
    """

    async def list_roles(
        request: Request,
        resource_id: str,
        current_user_id: str = Depends(get_current_user_id),
        service: RoleService = Depends(get_role_service),
        authorization: AuthorizationService = Depends(get_authorization_service),
    ):
        await authorization.require(current_user_id, ROLE_VIEW, scope, resource_id)
        roles = await service.list_roles(scope_type=scope, scope_id=resource_id)
        return ok(request, data=[to_role_view(role) for role in roles])

    async def create_role(
        request: Request,
        resource_id: str,
        body: CreateRoleRequest,
        current_user_id: str = Depends(get_current_user_id),
        service: RoleService = Depends(get_role_service),
        authorization: AuthorizationService = Depends(get_authorization_service),
    ):
        await authorization.require(current_user_id, ROLE_MANAGE, scope, resource_id)
        role = await service.create_role(
            scope_type=scope,
            scope_id=resource_id,
            name=body.name,
            permissions=body.permissions,
            priority=body.priority,
            created_by=current_user_id,
        )
        return ok(request, data=to_role_view(role), status_code=201)

    async def update_role(
        request: Request,
        resource_id: str,
        role_id: str,
        body: UpdateRoleRequest,
        current_user_id: str = Depends(get_current_user_id),
        service: RoleService = Depends(get_role_service),
        authorization: AuthorizationService = Depends(get_authorization_service),
    ):
        await authorization.require(current_user_id, ROLE_MANAGE, scope, resource_id)
        await _require_role_in_scope(
            service, role_id=role_id, scope=scope, resource_id=resource_id
        )
        role = await service.update_role(
            role_id=role_id,
            name=body.name,
            permissions=body.permissions,
            priority=body.priority,
        )
        return ok(request, data=to_role_view(role))

    async def delete_role(
        resource_id: str,
        role_id: str,
        current_user_id: str = Depends(get_current_user_id),
        service: RoleService = Depends(get_role_service),
        authorization: AuthorizationService = Depends(get_authorization_service),
    ) -> None:
        await authorization.require(current_user_id, ROLE_MANAGE, scope, resource_id)
        await _require_role_in_scope(
            service, role_id=role_id, scope=scope, resource_id=resource_id
        )
        await service.delete_role(role_id)

    collection = f"/{segment}/{{resource_id}}/roles"
    roles_router.add_api_route(
        collection,
        list_roles,
        methods=["GET"],
        name=f"list_{scope}_roles",
        response_model=SuccessResponse[list[RoleView]],
    )
    roles_router.add_api_route(
        collection,
        create_role,
        methods=["POST"],
        name=f"create_{scope}_role",
        status_code=201,
        response_model=SuccessResponse[RoleView],
    )
    roles_router.add_api_route(
        f"{collection}/{{role_id}}",
        update_role,
        methods=["PATCH"],
        name=f"update_{scope}_role",
        response_model=SuccessResponse[RoleView],
    )
    roles_router.add_api_route(
        f"{collection}/{{role_id}}",
        delete_role,
        methods=["DELETE"],
        name=f"delete_{scope}_role",
        status_code=204,
    )


for _segment, _scope in _SCOPES.items():
    _mount(_segment, _scope)


@roles_router.put(
    "/relationships/{relationship_id}/roles",
    response_model=SuccessResponse[RelationshipView],
)
async def assign_roles(
    request: Request,
    relationship_id: str,
    body: AssignRolesRequest,
    current_user_id: str = Depends(get_current_user_id),
    service: RoleService = Depends(get_role_service),
    authorization: AuthorizationService = Depends(get_authorization_service),
):
    """Replace the roles on a membership — roles belong to memberships (§89)."""
    relationships = RelationshipsRepository()
    membership = await relationships.find_by_id(relationship_id)
    if membership is None or membership.kind != "membership":
        raise AppError(
            code="MEMBERSHIP_NOT_FOUND",
            message="Membership not found",
            status_code=404,
        )
    scope: RoleScopeType = membership.target_type  # type: ignore[assignment]
    if scope not in _SCOPES.values():
        raise AppError(
            code="MEMBERSHIP_INVALID_TARGET",
            message="Only resource memberships carry roles",
            status_code=400,
        )
    resource_id = str(membership.target_id)
    await authorization.require(current_user_id, ROLE_MANAGE, scope, resource_id)

    # Every assigned role must belong to this resource, or a role from another
    # scope would silently grant permissions here.
    for role_id in body.role_ids:
        await _require_role_in_scope(
            service, role_id=role_id, scope=scope, resource_id=resource_id
        )

    updated = await relationships.find_one_and_update(
        {"_id": membership.id},
        {"$set": {"role_ids": [str(role_id) for role_id in body.role_ids]}},
    )
    assert updated is not None
    return ok(request, data=to_relationship_view(updated))


__all__ = ["RolesRepository", "roles_router"]
