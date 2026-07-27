from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from app.db.models.notification import NotificationKind, NotificationResourceType
from app.db.object_id import StrId

NotificationLevel = Literal["all", "mentions", "none"]
PushPlatform = Literal["ios", "android", "web"]


class NotificationView(BaseModel):
    id: StrId
    user_id: StrId
    kind: NotificationKind
    actor_user_id: StrId
    resource_type: NotificationResourceType
    resource_id: StrId
    message_id: StrId | None = None
    read_at: datetime | None = None
    data: dict[str, object] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime


class ConversationNotificationSettingsRequest(BaseModel):
    notification_level: NotificationLevel | None = None
    muted_until: datetime | None = None

    @model_validator(mode="after")
    def require_some_field(self) -> "ConversationNotificationSettingsRequest":
        if not self.model_fields_set:
            raise ValueError(
                "At least one of notification_level or muted_until is required"
            )
        return self


class NotificationPreferencesRequest(BaseModel):
    timezone: str | None = Field(default=None, max_length=80)
    dnd_from: str | None = Field(default=None, pattern=r"^\d{2}:\d{2}$")
    dnd_to: str | None = Field(default=None, pattern=r"^\d{2}:\d{2}$")
    notification_keywords: list[str] | None = Field(default=None, max_length=50)

    @model_validator(mode="after")
    def require_some_field(self) -> "NotificationPreferencesRequest":
        if not self.model_fields_set:
            raise ValueError(
                "At least one notification preference field is required"
            )
        return self


class PushTokenRegisterRequest(BaseModel):
    device_id: str | None = Field(default=None, max_length=200)
    platform: PushPlatform
    token: str = Field(min_length=1, max_length=4096)


class PushTokenView(BaseModel):
    id: StrId
    user_id: StrId
    device_id: str | None = None
    platform: PushPlatform
    token: str
    created_at: datetime
    updated_at: datetime
