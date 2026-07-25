from __future__ import annotations

from app.modules.notifications.dependencies import get_notifications_service
from app.modules.spaces.repository import SpacesRepository
from app.modules.spaces.service import SpacesService

def get_spaces_service() -> SpacesService:
    return SpacesService(
        repo=SpacesRepository(),
        notifications_service=get_notifications_service(),
    )
