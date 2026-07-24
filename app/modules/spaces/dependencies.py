from __future__ import annotations

from app.modules.spaces.repository import SpacesRepository
from app.modules.spaces.service import SpacesService

def get_spaces_service() -> SpacesService:
    return SpacesService(repo=SpacesRepository())
