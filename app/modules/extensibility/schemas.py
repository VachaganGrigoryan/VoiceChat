from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.db.object_id import StrId


class CreateWebhookRequest(BaseModel):
    direction: Literal["incoming", "outgoing"]
    target_type: Literal["conversation", "space"]
    target_id: str
    url: str | None = Field(default=None, max_length=2048)
    events: list[str] = Field(default_factory=list)


class WebhookView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: StrId
    direction: Literal["incoming", "outgoing"]
    target_type: Literal["conversation", "space"]
    target_id: StrId
    url: str | None = None
    created_by: StrId
    events: list[str]
    active: bool
    created_at: datetime
    updated_at: datetime


class RegisterSlashCommandRequest(BaseModel):
    trigger: str = Field(min_length=1, max_length=32)
    handler_url: str = Field(min_length=1, max_length=2048)
    description: str | None = Field(default=None, max_length=255)


class SlashCommandView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: StrId
    trigger: str
    handler_url: str
    description: str | None = None
    created_by: StrId
    active: bool
    created_at: datetime
    updated_at: datetime


class SubmitReportRequest(BaseModel):
    target_type: Literal["message", "user", "conversation"]
    target_id: str
    reason: str = Field(min_length=1, max_length=1000)


class ReportView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: StrId
    target_type: Literal["message", "user", "conversation"]
    target_id: StrId
    reporter_id: StrId
    reason: str
    status: Literal["pending", "resolved", "dismissed"]
    resolved_by: StrId | None = None
    resolved_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class ResolveReportRequest(BaseModel):
    status: Literal["resolved", "dismissed"]


class AuditLogView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: StrId
    actor_id: StrId
    action: str
    target_type: str | None = None
    target_id: StrId | None = None
    space_id: StrId | None = None
    data: dict[str, Any]
    created_at: datetime
