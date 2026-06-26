from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

from pymongo import AsyncMongoClient
from pymongo.asynchronous.database import AsyncDatabase

from beanie import init_beanie

from app.db.init import DOCUMENT_MODELS

# Beanie requires init_beanie (which normally talks to a live server) before any
# Document can be constructed or validated. Unit tests don't have a Mongo server,
# but they only need the models *registered* — not real I/O. We stub the two
# metadata calls init_beanie makes (buildInfo + list_collection_names) and skip
# index creation so models can be constructed/validated fully offline.
_initialized = False


def _ensure_beanie_initialized() -> None:
    global _initialized
    if _initialized:
        return

    client: AsyncMongoClient = AsyncMongoClient(
        "mongodb://localhost:27017", serverSelectionTimeoutMS=200
    )
    with (
        patch.object(
            AsyncDatabase, "command", new=AsyncMock(return_value={"version": "7.0.0"})
        ),
        patch.object(
            AsyncDatabase, "list_collection_names", new=AsyncMock(return_value=[])
        ),
    ):
        asyncio.get_event_loop().run_until_complete(
            init_beanie(
                database=client["voicechat_unittest"],
                document_models=DOCUMENT_MODELS,
                skip_indexes=True,
            )
        )
    _initialized = True


_ensure_beanie_initialized()
