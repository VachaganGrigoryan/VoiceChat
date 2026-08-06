from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from app.modules.realtime.presence.base import PresenceState


class PresenceStatusResponse(BaseModel):
    user_id: str
    state: PresenceState = "offline"
    is_online: bool = False
    last_seen_at: datetime | None = None
