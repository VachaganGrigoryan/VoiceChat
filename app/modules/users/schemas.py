from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.db.object_id import StrId


class UserProfileResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: StrId
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
    status_emoji: str | None = None
    status_text: str | None = None
    status_expires_at: datetime | None = None
    pronouns: str | None = None
    timezone: str | None = None
    dnd_from: str | None = None
    dnd_to: str | None = None
    notification_keywords: list[str] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class SelectedUserProfileResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: StrId
    username: str
    display_name: str | None = None
    bio: str | None = None
    avatar: dict | None = None
    status_emoji: str | None = None
    status_text: str | None = None
    status_expires_at: datetime | None = None
    pronouns: str | None = None
    timezone: str | None = None
    is_online: bool = False


class UpdateProfileRequest(BaseModel):
    display_name: str | None = Field(default=None, max_length=80)
    bio: str | None = Field(default=None, max_length=300)
    is_private: bool | None = None
    default_discovery_enabled: bool | None = None


class UpdateUsernameRequest(BaseModel):
    username: str = Field(min_length=3, max_length=30)
