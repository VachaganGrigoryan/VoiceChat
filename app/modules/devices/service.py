from __future__ import annotations

from app.core.errors import AppError
from app.db.models import DeviceDocument
from app.modules.devices.repository import DevicesRepository
from app.modules.devices.schemas import (
    DeviceView,
    PreKeyBundle,
    PreKeyInput,
    RegisterDeviceRequest,
)


class DevicesService:
    """Device + prekey scaffolding. Stores opaque key material only.

    No cryptography, key agreement, or signature verification is performed here —
    this is the storage/distribution surface for a future E2EE change.
    """

    def __init__(self, repo: DevicesRepository) -> None:
        self.repo = repo

    async def register_device(
        self, *, user_id: str, body: RegisterDeviceRequest
    ) -> DeviceView:
        device = await self.repo.upsert_device(
            user_id=user_id,
            device_id=body.device_id,
            name=body.name,
            platform=body.platform,
            identity_public_key=body.identity_public_key,
            signing_public_key=body.signing_public_key,
            registration_id=body.registration_id,
        )
        return self._to_view(device)

    async def upload_prekeys(
        self, *, user_id: str, device_id: str, prekeys: list[PreKeyInput]
    ) -> int:
        device = await self.repo.get_device(user_id=user_id, device_id=device_id)
        if device is None:
            raise AppError(
                code="DEVICE_NOT_FOUND",
                message="Device not found",
                status_code=404,
            )
        return await self.repo.add_prekeys(
            user_id=user_id,
            device_id=device_id,
            prekeys=[pk.model_dump() for pk in prekeys],
        )

    async def get_prekey_bundle(
        self, *, user_id: str, device_id: str | None = None
    ) -> PreKeyBundle:
        if device_id is not None:
            device = await self.repo.get_device(user_id=user_id, device_id=device_id)
        else:
            device = await self.repo.first_device_for_user(user_id=user_id)
        if device is None:
            raise AppError(
                code="DEVICE_NOT_FOUND",
                message="No registered device for user",
                status_code=404,
            )

        prekey = await self.repo.take_one_time_prekey(
            user_id=user_id, device_id=device.device_id
        )
        one_time = (
            PreKeyInput(
                key_id=prekey.key_id,
                public_key=prekey.public_key,
                signature=prekey.signature,
                one_time=prekey.one_time,
            )
            if prekey is not None
            else None
        )

        return PreKeyBundle(
            user_id=str(device.user_id),
            device_id=device.device_id,
            identity_public_key=device.identity_public_key,
            signing_public_key=device.signing_public_key,
            registration_id=device.registration_id,
            one_time_prekey=one_time,
        )

    def _to_view(self, device: DeviceDocument) -> DeviceView:
        return DeviceView(
            id=device.str_id,
            user_id=str(device.user_id),
            device_id=device.device_id,
            name=device.name,
            platform=device.platform,
            identity_public_key=device.identity_public_key,
            signing_public_key=device.signing_public_key,
            registration_id=device.registration_id,
            created_at=device.created_at,
            updated_at=device.updated_at,
        )
