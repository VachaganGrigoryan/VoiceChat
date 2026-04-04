from __future__ import annotations

import os

os.environ["ENV_FILE"] = ".env.test"

from motor.motor_asyncio import AsyncIOMotorClient

from app.core.config import settings
from app.core.rate_limit.limiter import rate_limiter
from app.db.mongo import connect_mongo, disconnect_mongo
from app.factory import create_app
from app.socket import create_socket_server, register_socket_events

import pytest_asyncio
from asgi_lifespan import LifespanManager
from httpx import AsyncClient, ASGITransport
from redis.asyncio import Redis

TEST_SERVER_URL = os.getenv("TEST_SERVER_URL", "http://api_test:8000")
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/1")


@pytest_asyncio.fixture(scope="function", autouse=True)
async def app_lifecycle():
    await connect_mongo()
    yield
    await disconnect_mongo()


@pytest_asyncio.fixture(autouse=True)
async def clean_redis():
    redis = Redis.from_url(REDIS_URL)
    await redis.flushdb()
    await redis.aclose()
    yield
    redis = Redis.from_url(REDIS_URL)
    await redis.flushdb()
    await redis.aclose()


@pytest_asyncio.fixture(autouse=True)
async def clean_rate_limits():
    await rate_limiter.storage.reset()
    yield
    await rate_limiter.storage.reset()


TEST_COLLECTIONS = [
    "users",
    "pings",
    "calls",
    "messages",
    "refresh_tokens",
    "verification_codes",
    "passkeys",
    "passkey_challenges",
    "discovery_tokens",
]


@pytest_asyncio.fixture(autouse=True)
async def clean_db():
    client = AsyncIOMotorClient(settings.mongo_uri)
    db = client[settings.mongo_db]

    for name in TEST_COLLECTIONS:
        await db[name].delete_many({})

    yield

    for name in TEST_COLLECTIONS:
        await db[name].delete_many({})

    client.close()


@pytest_asyncio.fixture(scope="function")
async def inprocess_client():
    app = create_app()
    sio = create_socket_server()
    register_socket_events(sio)
    app.state.sio = sio

    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport,
            base_url="http://testserver",
            timeout=10,
        ) as ac:
            yield ac

    redis_client = getattr(getattr(sio, "manager", None), "redis", None)
    if redis_client is not None:
        await redis_client.aclose()


@pytest_asyncio.fixture
async def live_client():
    async with AsyncClient(base_url=TEST_SERVER_URL, timeout=10) as ac:
        yield ac
