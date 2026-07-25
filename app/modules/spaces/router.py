from __future__ import annotations

from typing import Annotated
import socketio
from fastapi import APIRouter, Depends
from fastapi.encoders import jsonable_encoder
from starlette.requests import Request

from app.core.deps import get_sio
from app.core.errors.openapi import build_error_responses
from app.core.http import SuccessResponse, ok
from app.core.rate_limit import rate_limit
from app.core.security import require_verified_user
from app.modules.spaces.dependencies import get_spaces_service
from app.modules.spaces.schemas import (
    SpaceCreateRequest,
    SpaceView,
    SpaceInviteLinkView,
    SpaceJoinRequestView,
    RedeemSpaceInviteResponse,
    CreateSpaceInviteRequest,
    SpaceUpdateRequest,
    SpaceChannelView,
    SpaceMemberView,
    SpaceUserInviteRequest,
)
from app.modules.spaces.service import SpacesService
from app.modules.realtime import emit_space_invite

router = APIRouter(
    prefix="/spaces",
    tags=["spaces"],
    responses=build_error_responses(400, 401, 403, 404, 409, 422, 500),
)

@router.post(
    "",
    status_code=201,
    response_model=SuccessResponse[SpaceView],
    dependencies=[Depends(rate_limit("10/minute", scope="space_create"))],
)
async def create_space(
    request: Request,
    body: SpaceCreateRequest,
    user=Depends(require_verified_user),
    service: SpacesService = Depends(get_spaces_service),
):
    space = await service.create_space(
        created_by=user.str_id,
        name=body.name,
        slug=body.slug,
        kind=body.kind,
        visibility=body.visibility,
        join_policy=body.join_policy,
        avatar=body.avatar,
        settings=body.settings,
    )
    return ok(request, data=space, status_code=201)

@router.get(
    "/me",
    response_model=SuccessResponse[list[SpaceView]],
    dependencies=[Depends(rate_limit("60/minute", scope="spaces_list_me"))],
)
async def list_my_spaces(
    request: Request,
    user=Depends(require_verified_user),
    service: SpacesService = Depends(get_spaces_service),
):
    spaces = await service.list_for_user(user_id=user.str_id)
    return ok(request, data=spaces)

@router.get(
    "/{space_id}",
    response_model=SuccessResponse[SpaceView],
    dependencies=[Depends(rate_limit("60/minute", scope="space_get"))],
)
async def get_space(
    request: Request,
    space_id: str,
    user=Depends(require_verified_user),
    service: SpacesService = Depends(get_spaces_service),
):
    space = await service.get_space(space_id=space_id, user_id=user.str_id)
    return ok(request, data=space)

@router.post(
    "/{space_id}/invites",
    status_code=201,
    response_model=SuccessResponse[SpaceInviteLinkView],
    dependencies=[Depends(rate_limit("20/minute", scope="space_invite_create"))],
)
async def create_invite(
    request: Request,
    space_id: str,
    body: CreateSpaceInviteRequest,
    user=Depends(require_verified_user),
    service: SpacesService = Depends(get_spaces_service),
):
    invite = await service.create_invite(
        actor_user_id=user.str_id,
        space_id=space_id,
        expires_at=body.expires_at,
        max_uses=body.max_uses,
        requires_approval=body.requires_approval,
    )
    return ok(request, data=invite, status_code=201)

@router.post(
    "/{space_id}/invites/user",
    status_code=201,
    response_model=SuccessResponse[SpaceInviteLinkView],
    dependencies=[Depends(rate_limit("20/minute", scope="space_invite_user"))],
)
async def invite_user(
    request: Request,
    space_id: str,
    body: SpaceUserInviteRequest,
    sio: Annotated[socketio.AsyncServer, Depends(get_sio)],
    user=Depends(require_verified_user),
    service: SpacesService = Depends(get_spaces_service),
):
    invite = await service.invite_user(
        actor_user_id=user.str_id,
        space_id=space_id,
        user_id=body.user_id,
    )
    await emit_space_invite(
        sio,
        to_user_id=body.user_id,
        payload=jsonable_encoder(invite),
    )
    return ok(request, data=invite, status_code=201)

@router.get(
    "/{space_id}/invites",
    response_model=SuccessResponse[list[SpaceInviteLinkView]],
    dependencies=[Depends(rate_limit("30/minute", scope="space_invites_list"))],
)
async def list_invites(
    request: Request,
    space_id: str,
    user=Depends(require_verified_user),
    service: SpacesService = Depends(get_spaces_service),
):
    invites = await service.list_invites(actor_user_id=user.str_id, space_id=space_id)
    return ok(request, data=invites)

@router.delete(
    "/{space_id}/invites/{invite_id}",
    status_code=204,
    dependencies=[Depends(rate_limit("30/minute", scope="space_invite_revoke"))],
)
async def revoke_invite(
    request: Request,
    space_id: str,
    invite_id: str,
    user=Depends(require_verified_user),
    service: SpacesService = Depends(get_spaces_service),
):
    await service.revoke_invite(
        actor_user_id=user.str_id,
        space_id=space_id,
        invite_id=invite_id,
    )
    return ok(request, data=None, status_code=204)

@router.post(
    "/invites/{code}/redeem",
    response_model=SuccessResponse[RedeemSpaceInviteResponse],
    dependencies=[Depends(rate_limit("20/minute", scope="space_invite_redeem"))],
)
async def redeem_invite(
    request: Request,
    code: str,
    user=Depends(require_verified_user),
    service: SpacesService = Depends(get_spaces_service),
):
    status, space, join_request = await service.redeem_invite(
        user_id=user.str_id,
        code=code,
    )
    return ok(
        request,
        data=RedeemSpaceInviteResponse(
            status=status,
            space=space,
            join_request=join_request,
        ),
    )

@router.post(
    "/{space_id}/join",
    status_code=201,
    response_model=SuccessResponse[SpaceJoinRequestView],
    dependencies=[Depends(rate_limit("20/minute", scope="space_join_request"))],
)
async def request_join(
    request: Request,
    space_id: str,
    user=Depends(require_verified_user),
    service: SpacesService = Depends(get_spaces_service),
):
    pending = await service.request_join(user_id=user.str_id, space_id=space_id)
    return ok(request, data=pending, status_code=201)

@router.get(
    "/{space_id}/join-requests",
    response_model=SuccessResponse[list[SpaceJoinRequestView]],
    dependencies=[Depends(rate_limit("30/minute", scope="space_join_requests_list"))],
)
async def list_join_requests(
    request: Request,
    space_id: str,
    user=Depends(require_verified_user),
    service: SpacesService = Depends(get_spaces_service),
):
    requests = await service.list_join_requests(actor_user_id=user.str_id, space_id=space_id)
    return ok(request, data=requests)

@router.post(
    "/{space_id}/join-requests/{request_id}/approve",
    response_model=SuccessResponse[SpaceJoinRequestView],
    dependencies=[Depends(rate_limit("30/minute", scope="space_join_approve"))],
)
async def approve_join_request(
    request: Request,
    space_id: str,
    request_id: str,
    user=Depends(require_verified_user),
    service: SpacesService = Depends(get_spaces_service),
):
    updated = await service.approve_join_request(
        actor_user_id=user.str_id,
        space_id=space_id,
        request_id=request_id,
    )
    return ok(request, data=updated)

@router.post(
    "/{space_id}/join-requests/{request_id}/reject",
    response_model=SuccessResponse[SpaceJoinRequestView],
    dependencies=[Depends(rate_limit("30/minute", scope="space_join_reject"))],
)
async def reject_join_request(
    request: Request,
    space_id: str,
    request_id: str,
    user=Depends(require_verified_user),
    service: SpacesService = Depends(get_spaces_service),
):
    updated = await service.reject_join_request(
        actor_user_id=user.str_id,
        space_id=space_id,
        request_id=request_id,
    )
    return ok(request, data=updated)

@router.get(
    "/{space_id}/channels",
    response_model=SuccessResponse[list[SpaceChannelView]],
    dependencies=[Depends(rate_limit("30/minute", scope="space_channels_list"))],
)
async def list_channels(
    request: Request,
    space_id: str,
    user=Depends(require_verified_user),
    service: SpacesService = Depends(get_spaces_service),
):
    channels = await service.list_channels(space_id=space_id, user_id=user.str_id)
    return ok(request, data=channels)

@router.post(
    "/{space_id}/channels/{conversation_id}/join",
    status_code=204,
    dependencies=[Depends(rate_limit("20/minute", scope="space_channel_join"))],
)
async def join_channel(
    request: Request,
    space_id: str,
    conversation_id: str,
    user=Depends(require_verified_user),
    service: SpacesService = Depends(get_spaces_service),
):
    await service.join_channel(
        space_id=space_id,
        conversation_id=conversation_id,
        user_id=user.str_id,
    )
    return ok(request, data=None, status_code=204)

@router.get(
    "/{space_id}/members",
    response_model=SuccessResponse[list[SpaceMemberView]],
    dependencies=[Depends(rate_limit("30/minute", scope="space_members_list"))],
)
async def list_members(
    request: Request,
    space_id: str,
    user=Depends(require_verified_user),
    service: SpacesService = Depends(get_spaces_service),
):
    members = await service.list_members(space_id=space_id, user_id=user.str_id)
    return ok(request, data=members)

@router.patch(
    "/{space_id}",
    response_model=SuccessResponse[SpaceView],
    dependencies=[Depends(rate_limit("20/minute", scope="space_update"))],
)
async def update_space(
    request: Request,
    space_id: str,
    body: SpaceUpdateRequest,
    user=Depends(require_verified_user),
    service: SpacesService = Depends(get_spaces_service),
):
    space = await service.update_space(
        actor_user_id=user.str_id,
        space_id=space_id,
        name=body.name,
        visibility=body.visibility,
        settings=body.settings,
    )
    return ok(request, data=space)
