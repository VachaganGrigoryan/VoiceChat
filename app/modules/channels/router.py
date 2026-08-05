from __future__ import annotations

from typing import Annotated, Literal, Optional

import socketio
from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from starlette.requests import Request

from app.core.deps import get_sio
from app.db.models.embedded import TextStyleDocument
from app.modules.authorization.capabilities import affected_viewer_ids
from app.modules.realtime import emit_capabilities_invalidated
from app.core.errors.openapi import build_error_responses
from app.core.http import (
    PaginatedResponse,
    PaginationMeta,
    SuccessResponse,
    ok,
    ok_paginated,
)
from app.core.rate_limit import rate_limit
from app.core.security import require_verified_user
from app.modules.channels.dependencies import get_channel_service
from app.modules.channels.schemas import (
    ChannelCreateRequest,
    ChannelInboxRow,
    ChannelInboxUpdateRequest,
    ChannelMemberView,
    ChannelMessageCreateRequest,
    ChannelNotificationUpdateRequest,
    ChannelUpdateRequest,
    ChannelView,
    ChannelViewerStateView,
)
from app.modules.channels.emit import fan_out_channel_message
from app.modules.channels.service import ChannelService
from app.modules.messages.schemas import MessageDoc, SendRichContentRequest
from app.modules.notifications.dependencies import get_notifications_service
from app.modules.notifications.service import NotificationsService

# Edits that can change what a member may do. A rename cannot, so it must not
# invalidate every member's capability cache.
_POLICY_FIELDS: frozenset[str] = frozenset(
    {"visibility", "join_policy", "posting_policy", "comment_policy"}
)

router = APIRouter(
    prefix="/channels",
    tags=["channels"],
    responses=build_error_responses(400, 401, 403, 404, 409, 422, 500),
)


@router.post(
    "",
    status_code=201,
    response_model=SuccessResponse[ChannelView],
    dependencies=[Depends(rate_limit("20/minute", scope="channel_create"))],
)
async def create_channel(
    request: Request,
    body: ChannelCreateRequest,
    user=Depends(require_verified_user),
    service: ChannelService = Depends(get_channel_service),
):
    channel = await service.create(
        created_by=user.str_id,
        name=body.name,
        slug=body.slug,
        kind=body.kind,
        description=body.description,
        visibility=body.visibility,
        join_policy=body.join_policy,
        posting_policy=body.posting_policy,
        comment_policy=body.comment_policy,
        tags=body.tags,
    )
    return ok(request, data=channel, status_code=201)


@router.get(
    "/me",
    response_model=SuccessResponse[list[ChannelInboxRow]],
    dependencies=[Depends(rate_limit("60/minute", scope="channel_inbox"))],
)
async def list_my_channels(
    request: Request,
    limit: int = Query(default=100, ge=1, le=200),
    user=Depends(require_verified_user),
    service: ChannelService = Depends(get_channel_service),
):
    """The caller's channels with per-user state, for the chat inbox."""
    rows = await service.list_for_inbox(user_id=user.str_id, limit=limit)
    return ok(request, data=rows)


@router.get(
    "/{channel_id}",
    response_model=SuccessResponse[ChannelView],
    dependencies=[Depends(rate_limit("60/minute", scope="channel_get"))],
)
async def get_channel(
    request: Request,
    channel_id: str,
    user=Depends(require_verified_user),
    service: ChannelService = Depends(get_channel_service),
):
    channel = await service.get(channel_id=channel_id, viewer_id=user.str_id)
    return ok(request, data=channel)


@router.patch(
    "/{channel_id}",
    response_model=SuccessResponse[ChannelView],
    dependencies=[Depends(rate_limit("30/minute", scope="channel_update"))],
)
async def update_channel(
    request: Request,
    channel_id: str,
    body: ChannelUpdateRequest,
    sio: Annotated[socketio.AsyncServer, Depends(get_sio)],
    user=Depends(require_verified_user),
    service: ChannelService = Depends(get_channel_service),
):
    updates = body.model_dump(exclude_unset=True)
    channel = await service.update(
        channel_id=channel_id,
        actor_user_id=user.str_id,
        updates=updates,
    )
    # Only a policy or visibility edit can change what members may do; renaming
    # a channel must not trigger a cache stampede.
    if _POLICY_FIELDS & set(updates):
        await emit_capabilities_invalidated(
            sio,
            to_user_ids=await affected_viewer_ids(
                resource_type="channel", resource_id=channel_id
            ),
            resource_type="channel",
            resource_id=channel_id,
        )
    return ok(request, data=channel)


@router.get(
    "/{channel_id}/messages",
    response_model=PaginatedResponse[list[MessageDoc]],
    dependencies=[Depends(rate_limit("60/minute", scope="channel_messages"))],
)
async def list_channel_messages(
    request: Request,
    channel_id: str,
    limit: int = Query(default=20, ge=1, le=100),
    cursor: Optional[str] = Query(default=None),
    user=Depends(require_verified_user),
    service: ChannelService = Depends(get_channel_service),
):
    items, next_cursor = await service.list_messages(
        channel_id=channel_id,
        viewer_id=user.str_id,
        limit=limit,
        cursor=cursor,
    )
    return ok_paginated(
        request,
        data=items,
        meta=PaginationMeta(cursor=cursor, next_cursor=next_cursor, limit=limit),
    )


@router.post(
    "/{channel_id}/messages",
    status_code=201,
    response_model=SuccessResponse[MessageDoc],
    dependencies=[Depends(rate_limit("60/minute", scope="channel_message_create"))],
)
async def create_channel_message(
    request: Request,
    channel_id: str,
    body: ChannelMessageCreateRequest,
    sio: Annotated[socketio.AsyncServer, Depends(get_sio)],
    user=Depends(require_verified_user),
    service: ChannelService = Depends(get_channel_service),
    notifications: NotificationsService = Depends(get_notifications_service),
):
    result = await service.create_message(
        channel_id=channel_id,
        sender_id=user.str_id,
        text=body.text,
        reply_mode=body.reply_mode,
        reply_to_message_id=body.reply_to_message_id,
        style=(
            TextStyleDocument(**body.style.model_dump()) if body.style else None
        ),
    )
    await fan_out_channel_message(
        sio,
        result=result,
        notifications=notifications,
    )
    return ok(request, data=result.message, status_code=201)


@router.post(
    "/{channel_id}/messages/media",
    status_code=201,
    response_model=SuccessResponse[MessageDoc],
    dependencies=[Depends(rate_limit("30/minute", scope="channel_media_upload"))],
)
async def send_channel_media(
    request: Request,
    channel_id: str,
    sio: Annotated[socketio.AsyncServer, Depends(get_sio)],
    type: Literal["media", "file"] = Form(...),
    media_kind: Optional[Literal["voice", "audio", "image", "video"]] = Form(None),
    duration_ms: Optional[int] = Form(None),
    text: Optional[str] = Form(None),
    reply_mode: Optional[Literal["quote", "thread"]] = Form(None),
    reply_to_message_id: Optional[str] = Form(None),
    file: UploadFile = File(...),
    user=Depends(require_verified_user),
    service: ChannelService = Depends(get_channel_service),
    notifications: NotificationsService = Depends(get_notifications_service),
):
    result = await service.upload_media(
        channel_id=channel_id,
        sender_id=user.str_id,
        message_type=type,
        media_kind=media_kind,
        file=file,
        text=text,
        duration_ms=duration_ms,
        reply_mode=reply_mode,
        reply_to_message_id=reply_to_message_id,
    )
    await fan_out_channel_message(
        sio,
        result=result,
        notifications=notifications,
    )
    return ok(request, data=result.message, status_code=201)


@router.post(
    "/{channel_id}/messages/content",
    status_code=201,
    response_model=SuccessResponse[MessageDoc],
    dependencies=[Depends(rate_limit("60/minute", scope="channel_rich_message"))],
)
async def send_channel_rich_content(
    request: Request,
    channel_id: str,
    body: SendRichContentRequest,
    sio: Annotated[socketio.AsyncServer, Depends(get_sio)],
    user=Depends(require_verified_user),
    service: ChannelService = Depends(get_channel_service),
    notifications: NotificationsService = Depends(get_notifications_service),
):
    result = await service.send_rich_content(
        channel_id=channel_id,
        sender_id=user.str_id,
        body=body,
    )
    await fan_out_channel_message(
        sio,
        result=result,
        notifications=notifications,
    )
    return ok(request, data=result.message, status_code=201)


@router.post(
    "/{channel_id}/messages/{message_id}/read",
    response_model=SuccessResponse[MessageDoc],
)
async def mark_channel_message_read(
    request: Request,
    channel_id: str,
    message_id: str,
    user=Depends(require_verified_user),
    service: ChannelService = Depends(get_channel_service),
):
    message = await service.mark_read(
        channel_id=channel_id,
        message_id=message_id,
        user_id=user.str_id,
    )
    return ok(request, data=message)


@router.post(
    "/{channel_id}/leave",
    response_model=SuccessResponse[bool],
    dependencies=[Depends(rate_limit("30/minute", scope="channel_leave"))],
)
async def leave_channel(
    request: Request,
    channel_id: str,
    user=Depends(require_verified_user),
    service: ChannelService = Depends(get_channel_service),
):
    left = await service.leave(channel_id=channel_id, user_id=user.str_id)
    return ok(request, data=left)


@router.get(
    "/{channel_id}/members",
    response_model=SuccessResponse[list[ChannelMemberView]],
    dependencies=[Depends(rate_limit("60/minute", scope="channel_members"))],
)
async def list_channel_members(
    request: Request,
    channel_id: str,
    user=Depends(require_verified_user),
    service: ChannelService = Depends(get_channel_service),
):
    members = await service.list_members(channel_id=channel_id, viewer_id=user.str_id)
    return ok(
        request,
        data=[
            ChannelMemberView(
                relationship_id=member.str_id,
                user_id=str(member.user_id),
                status=member.status,
                role_ids=[str(role_id) for role_id in member.role_ids],
                requested_at=member.requested_at,
                activated_at=member.activated_at,
            )
            for member in members
        ],
    )


@router.patch(
    "/{channel_id}/inbox",
    response_model=SuccessResponse[ChannelViewerStateView],
    dependencies=[Depends(rate_limit("60/minute", scope="channel_inbox_update"))],
)
async def update_channel_inbox_state(
    request: Request,
    channel_id: str,
    body: ChannelInboxUpdateRequest,
    user=Depends(require_verified_user),
    service: ChannelService = Depends(get_channel_service),
):
    state = await service.update_viewer_state(
        channel_id=channel_id,
        user_id=user.str_id,
        updates=body.model_dump(exclude_unset=True),
    )
    return ok(request, data=state)


@router.patch(
    "/{channel_id}/notifications",
    response_model=SuccessResponse[ChannelViewerStateView],
    dependencies=[Depends(rate_limit("60/minute", scope="channel_notifications"))],
)
async def update_channel_notifications(
    request: Request,
    channel_id: str,
    body: ChannelNotificationUpdateRequest,
    user=Depends(require_verified_user),
    service: ChannelService = Depends(get_channel_service),
):
    state = await service.update_viewer_state(
        channel_id=channel_id,
        user_id=user.str_id,
        updates=body.model_dump(exclude_unset=True),
    )
    return ok(request, data=state)


@router.post(
    "/{channel_id}/read",
    response_model=SuccessResponse[ChannelViewerStateView],
    dependencies=[Depends(rate_limit("60/minute", scope="channel_read"))],
)
async def mark_channel_read(
    request: Request,
    channel_id: str,
    user=Depends(require_verified_user),
    service: ChannelService = Depends(get_channel_service),
):
    state = await service.mark_channel_read(channel_id=channel_id, user_id=user.str_id)
    return ok(request, data=state)
