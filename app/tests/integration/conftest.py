from __future__ import annotations

import os

from app.asgi import app
from app.db.mongo import get_db, connect_mongo, disconnect_mongo

os.environ["ENV_FILE"] = ".env.test"

import pytest_asyncio
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


@pytest_asyncio.fixture(scope="function", autouse=True)
async def clean_db():
    db = get_db()
    for name in ["users", "verification_codes", "messages", "refresh_tokens"]:
        await db[name].delete_many({})
    yield
    for name in ["users", "verification_codes", "messages", "refresh_tokens"]:
        await db[name].delete_many({})


@pytest_asyncio.fixture(scope="function", autouse=True)
async def inprocess_client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver", timeout=10) as ac:
        yield ac


@pytest_asyncio.fixture
async def live_client():
    async with AsyncClient(base_url=TEST_SERVER_URL, timeout=10) as ac:
        yield ac