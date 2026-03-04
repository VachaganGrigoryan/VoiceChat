from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase

from app.core.config import settings


@dataclass
class MongoState:
    client: AsyncIOMotorClient
    db: AsyncIOMotorDatabase


_mongo_state: Optional[MongoState] = None


async def connect_mongo() -> MongoState:
    """
    Creates a single Mongo client for the whole app.
    Motor client is thread-safe and intended to be shared.
    """
    global _mongo_state
    if _mongo_state is not None:
        return _mongo_state

    client = AsyncIOMotorClient(
        settings.mongo_uri,
        # serverSelectionTimeoutMS=5000,  # optional: fail fast on startup
        # uuidRepresentation="standard",  # optional: if you use UUIDs
    )
    db = client[settings.mongo_db]
    _mongo_state = MongoState(client=client, db=db)
    return _mongo_state


async def disconnect_mongo() -> None:
    global _mongo_state
    if _mongo_state is None:
        return
    _mongo_state.client.close()
    _mongo_state = None


def get_db() -> AsyncIOMotorDatabase:
    """
    Use in FastAPI dependencies or services.
    Requires connect_mongo() to have run during startup.
    """
    if _mongo_state is None:
        raise RuntimeError("MongoDB is not initialized. Did startup run?")
    return _mongo_state.db