from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query
from starlette.requests import Request

from app.core.errors.openapi import build_error_responses
from app.core.http import SuccessResponse, ok
from app.core.rate_limit import rate_limit
from app.core.security import require_verified_user
from app.modules.devices.dependencies import get_devices_service
from app.modules.devices.schemas import (
    DeviceView,
    PreKeyBundle,
    RegisterDeviceRequest,
    UploadPreKeysRequest,
    UploadPreKeysResponse,
)
from app.modules.devices.service import DevicesService

router = APIRouter(
    tags=["devices"],
    responses=build_error_responses(400, 401, 422, 500),
)


@router.post(
    "/devices",
    status_code=201,
    response_model=SuccessResponse[DeviceView],
    dependencies=[Depends(rate_limit("30/minute", scope="device_register"))],
)
async def register_device(
    request: Request,
    body: RegisterDeviceRequest,
    user=Depends(require_verified_user),
    service: DevicesService = Depends(get_devices_service),
):
    device = await service.register_device(user_id=user.str_id, body=body)
    return ok(request, data=device, status_code=201)


@router.post(
    "/devices/{device_id}/prekeys",
    status_code=201,
    response_model=SuccessResponse[UploadPreKeysResponse],
    dependencies=[Depends(rate_limit("30/minute", scope="device_prekeys"))],
)
async def upload_prekeys(
    request: Request,
    device_id: str,
    body: UploadPreKeysRequest,
    user=Depends(require_verified_user),
    service: DevicesService = Depends(get_devices_service),
):
    uploaded = await service.upload_prekeys(
        user_id=user.str_id, device_id=device_id, prekeys=body.prekeys
    )
    return ok(
        request,
        data=UploadPreKeysResponse(device_id=device_id, uploaded=uploaded),
        status_code=201,
    )


@router.get(
    "/users/{user_id}/prekey-bundle",
    response_model=SuccessResponse[PreKeyBundle],
    dependencies=[Depends(rate_limit("60/minute", scope="prekey_bundle"))],
)
async def get_prekey_bundle(
    request: Request,
    user_id: str,
    device_id: Optional[str] = Query(None),
    user=Depends(require_verified_user),
    service: DevicesService = Depends(get_devices_service),
):
    bundle = await service.get_prekey_bundle(user_id=user_id, device_id=device_id)
    return ok(request, data=bundle)
