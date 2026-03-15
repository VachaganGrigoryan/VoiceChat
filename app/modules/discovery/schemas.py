from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


DiscoveryTokenType = Literal["code", "link"]


class DiscoveryUserSummary(BaseModel):
    id: str
    username: str
    display_name: str | None = None
    avatar: dict | None = None
    is_online: bool = False
    can_ping: bool = False
    chat_allowed: bool = False
    ping_status: str = "none"
    discovered_via: Literal["username", "code", "link"] | None = None


class RegenerateCodeResponse(BaseModel):
    code: str
    token_preview: str
    expires_at: datetime | None = None


class CreateInviteLinkRequest(BaseModel):
    expires_in_seconds: int | None = Field(default=None, ge=60, le=60 * 60 * 24 * 30)
    max_uses: int | None = Field(default=None, ge=1, le=1000)


class CreateInviteLinkResponse(BaseModel):
    token: str
    url: str
    expires_at: datetime | None = None
    max_uses: int | None = None


class ResolveCodeRequest(BaseModel):
    code: str = Field(min_length=4, max_length=32)