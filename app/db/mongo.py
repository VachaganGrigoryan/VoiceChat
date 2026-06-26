from __future__ import annotations

from dataclasses import dataclass

from pymongo import AsyncMongoClient
from pymongo.asynchronous.database import AsyncDatabase

from app.core.config import settings


@dataclass
class MongoState:
    client: AsyncMongoClient
    db: AsyncDatabase


_mongo_state: MongoState | None = None


async def connect_mongo() -> MongoState:
    """
    Creates a single PyMongo Async client for the whole app.
    The client is safe to share across the event loop.
    """
    global _mongo_state
    if _mongo_state is not None:
        return _mongo_state

    client = AsyncMongoClient(
        settings.mongo_uri,
        appname=f"voicechat-{settings.app_env}",
        serverSelectionTimeoutMS=settings.mongo_server_selection_timeout_ms,
        maxPoolSize=settings.mongo_max_pool_size,
        minPoolSize=settings.mongo_min_pool_size,
    )
    db = client[settings.mongo_db]
    _mongo_state = MongoState(client=client, db=db)
    return _mongo_state


async def disconnect_mongo() -> None:
    global _mongo_state
    if _mongo_state is None:
        return
    await _mongo_state.client.close()
    _mongo_state = None


def get_db() -> AsyncDatabase:
    """
    Use in FastAPI dependencies or services.
    Requires connect_mongo() to have run during startup.
    """
    if _mongo_state is None:
        raise RuntimeError("MongoDB is not initialized. Did startup run?")
    return _mongo_state.db
