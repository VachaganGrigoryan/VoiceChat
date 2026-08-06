"""Policy-filtered browse over spaces, channels, groups and people.

Separate from `discovery` (people and profile privacy) and `search` (message
full text). This module answers "what exists that I may see", cursor-paginated
like every other list surface.
"""

from __future__ import annotations

from app.modules.directory.repository import DirectoryRepository
from app.modules.directory.router import router as directory_router
from app.modules.directory.service import DirectoryService

__all__ = ["DirectoryRepository", "DirectoryService", "directory_router"]
