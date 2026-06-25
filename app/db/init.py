from __future__ import annotations

from beanie import init_beanie

from app.db.models import DOCUMENT_MODELS
from app.db.mongo import get_db


async def init_database() -> None:
    """Initialize Beanie for all document models.

    Beanie builds each collection's indexes (from its `Settings.indexes`) here.
    """
    await init_beanie(database=get_db(), document_models=DOCUMENT_MODELS)
