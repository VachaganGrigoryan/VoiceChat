"""Roles APIs (RefactoringPlan §89).

Roles are scoped to a resource, so they are listed and created under that
resource. Assignment lives on the membership, because a role only means
anything through an active membership (§50).
"""

from __future__ import annotations

from typing import Annotated

import socketio
from fastapi import APIRouter, Depends
from starlette.requests import Request

from app.core.deps import get_sio
from app.core.errors import AppError
from app.core.errors.openapi import build_error_responses
from app.core.http import SuccessResponse, ok
from app.core.security import get_current_user_id
from app.db.models.role import RoleScopeType
from app.modules.authorization.permissions import ROLE_MANAGE, ROLE_VIEW
from app.modules.authorization.repository import RolesRepository
from app.modules.authorization.roles import RoleService
from app.modules.authorization.capabilities import (
    CapabilitiesService,
    affected_viewer_ids,
)
from app.modules.authorization.schemas import (
    AssignRolesRequest,
    CapabilitiesRequest,
    CapabilitiesResponse,
    CreateRoleRequest,
    ResourceCapabilitiesView,
    RoleView,
    UpdateRoleRequest,
    to_capabilities_view,
    to_role_view,
)
from app.modules.authorization.service import AuthorizationService
from app.modules.relationships.repository import RelationshipsRepository
from app.modules.realtime import emit_capabilities_invalidated
from app.modules.relationships.schemas import RelationshipView, to_relationship_view

_RESPONSES = build_error_responses(400, 401, 403, 404, 409, 422, 500)

roles_router = APIRouter(tags=["roles"], responses=_RESPONSES)

capabilities_router = APIRouter(tags=["capabilities"], responses=_RESPONSES)

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


async def _invalidate_resource(
    sio: socketio.AsyncServer, *, scope: RoleScopeType, resource_id: str
) -> None:
    """A role definition changed, so every member's permissions may have.

    Bounded to active members: a stranger holds no cached entry to invalidate.
    """
    await emit_capabilities_invalidated(
        sio,
        to_user_ids=await affected_viewer_ids(
            resource_type=scope, resource_id=resource_id
        ),
        resource_type=scope,
        resource_id=resource_id,
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
        sio: Annotated[socketio.AsyncServer, Depends(get_sio)],
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
        await _invalidate_resource(sio, scope=scope, resource_id=resource_id)
        return ok(request, data=to_role_view(role), status_code=201)

    async def update_role(
        request: Request,
        resource_id: str,
        role_id: str,
        body: UpdateRoleRequest,
        sio: Annotated[socketio.AsyncServer, Depends(get_sio)],
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
        await _invalidate_resource(sio, scope=scope, resource_id=resource_id)
        return ok(request, data=to_role_view(role))

    async def delete_role(
        resource_id: str,
        role_id: str,
        sio: Annotated[socketio.AsyncServer, Depends(get_sio)],
        current_user_id: str = Depends(get_current_user_id),
        service: RoleService = Depends(get_role_service),
        authorization: AuthorizationService = Depends(get_authorization_service),
    ) -> None:
        await authorization.require(current_user_id, ROLE_MANAGE, scope, resource_id)
        await _require_role_in_scope(
            service, role_id=role_id, scope=scope, resource_id=resource_id
        )
        await service.delete_role(role_id)
        await _invalidate_resource(sio, scope=scope, resource_id=resource_id)

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
    sio: Annotated[socketio.AsyncServer, Depends(get_sio)],
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
    # The subject's permissions just changed; without this they would keep
    # rendering the old affordances until their cache went stale.
    await emit_capabilities_invalidated(
        sio,
        to_user_ids=[str(updated.user_id)],
        resource_type=scope,
        resource_id=resource_id,
    )
    return ok(request, data=to_relationship_view(updated))


def get_capabilities_service() -> CapabilitiesService:
    return CapabilitiesService()


@capabilities_router.post(
    "/viewer/capabilities",
    response_model=SuccessResponse[CapabilitiesResponse],
)
async def resolve_viewer_capabilities(
    request: Request,
    body: CapabilitiesRequest,
    current_user_id: str = Depends(get_current_user_id),
    service: CapabilitiesService = Depends(get_capabilities_service),
):
    """What the caller may do across a batch of resources.

    POST rather than GET because a fifty-reference body does not belong in a
    query string, and the answer is per-viewer, so nothing downstream could
    cache it anyway. The batch limit is enforced by the request schema, which
    returns 422 above it.
    """
    results = await service.resolve(
        user_id=current_user_id,
        resources=[(ref.type, ref.id) for ref in body.resources],
        actions=body.actions,
    )
    return ok(
        request,
        data=CapabilitiesResponse(
            capabilities=[to_capabilities_view(result) for result in results]
        ),
    )


def _register_resource_capabilities() -> None:
    """One `GET .../capabilities` per scope, for cold deep links.

    Same service as the batch endpoint — a deep link that resolves its own
    permissions must not be able to get a different answer than the inbox did.
    """

    for segment, scope in _SCOPES.items():

        def make_handler(scope: RoleScopeType = scope):
            async def handler(
                request: Request,
                resource_id: str,
                current_user_id: str = Depends(get_current_user_id),
                service: CapabilitiesService = Depends(get_capabilities_service),
            ):
                results = await service.resolve(
                    user_id=current_user_id, resources=[(scope, resource_id)]
                )
                return ok(request, data=to_capabilities_view(results[0]))

            return handler

        capabilities_router.add_api_route(
            f"/{segment}/{{resource_id}}/capabilities",
            make_handler(),
            methods=["GET"],
            response_model=SuccessResponse[ResourceCapabilitiesView],
            name=f"get_{scope}_capabilities",
        )


_register_resource_capabilities()


__all__ = ["RolesRepository", "capabilities_router", "roles_router"]
