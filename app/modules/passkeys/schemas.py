from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class RegisterPasskeyStartRequest(BaseModel):
    nickname: str | None = Field(default=None, max_length=120)


class RegisterPasskeyFinishRequest(BaseModel):
    credential: dict[str, Any]
    nickname: str | None = Field(default=None, max_length=120)


class LoginPasskeyStartRequest(BaseModel):
    email: EmailStr | None = None


class LoginPasskeyFinishRequest(BaseModel):
    email: EmailStr | None = None
    credential: dict[str, Any]


class PasskeyRegistrationOptionsPayload(BaseModel):
    model_config = ConfigDict(extra="allow")

    nickname: str | None = None


class PasskeyAuthenticationOptionsPayload(BaseModel):
    model_config = ConfigDict(extra="allow")


class PasskeyResponse(BaseModel):
    credential_id: str
    nickname: str | None = None
    transports: list[str] | None = None
    device_type: str | None = None
    backed_up: bool | None = None
    aaguid: str | None = None
    created_at: datetime
    last_used_at: datetime | None = None


class PasskeysListResponse(BaseModel):
    items: list[PasskeyResponse]


class PasskeyDeleteResult(BaseModel):
    deleted: bool


class AuthTokensResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class ChallengeDocument(BaseModel):
    id: str | None = None
    user_id: str | None = None
    email: str | None = None
    flow: Literal["register", "authenticate"]
    challenge: str
    expires_at: datetime
    used_at: datetime | None = None
    created_at: datetime


class PasskeyDocument(BaseModel):
    id: str | None = None
    user_id: str
    credential_id: str
    public_key: str
    sign_count: int
    transports: list[str] | None = None
    device_type: str | None = None
    backed_up: bool | None = None
    nickname: str | None = None
    aaguid: str | None = None
    last_used_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
