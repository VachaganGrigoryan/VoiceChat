from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class UserProfileResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    email: str
    is_verified: bool
    username: str
    display_name: str | None = None
    bio: str | None = None
    avatar: dict | None = None
    is_private: bool
    default_discovery_enabled: bool
    last_seen_at: datetime | None = None
    username_updated_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class SelectedUserProfileResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    username: str
    display_name: str | None = None
    bio: str | None = None
    avatar: dict | None = None
    is_online: bool = False


class UpdateProfileRequest(BaseModel):
    display_name: str | None = Field(default=None, max_length=80)
    bio: str | None = Field(default=None, max_length=300)
    is_private: bool | None = None
    default_discovery_enabled: bool | None = None


class UpdateUsernameRequest(BaseModel):
    username: str = Field(min_length=3, max_length=30)
