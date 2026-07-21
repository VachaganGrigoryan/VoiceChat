from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.db.object_id import StrId


class RegisterDeviceRequest(BaseModel):
    device_id: str = Field(min_length=1, max_length=128)
    name: str | None = Field(default=None, max_length=128)
    platform: str | None = Field(default=None, max_length=64)
    identity_public_key: str | None = None
    signing_public_key: str | None = None
    registration_id: int | None = Field(default=None, ge=0)


class DeviceView(BaseModel):
    id: StrId
    user_id: StrId
    device_id: str
    name: str | None = None
    platform: str | None = None
    identity_public_key: str | None = None
    signing_public_key: str | None = None
    registration_id: int | None = None
    created_at: datetime
    updated_at: datetime


class PreKeyInput(BaseModel):
    key_id: int = Field(ge=0)
    public_key: str = Field(min_length=1)
    signature: str | None = None
    one_time: bool = True


class UploadPreKeysRequest(BaseModel):
    prekeys: list[PreKeyInput] = Field(min_length=1, max_length=200)


class UploadPreKeysResponse(BaseModel):
    device_id: str
    uploaded: int


class PreKeyBundle(BaseModel):
    user_id: StrId
    device_id: str
    identity_public_key: str | None = None
    signing_public_key: str | None = None
    registration_id: int | None = None
    one_time_prekey: PreKeyInput | None = None
