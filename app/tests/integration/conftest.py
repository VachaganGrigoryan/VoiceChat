from __future__ import annotations

import os

os.environ["ENV_FILE"] = ".env.test"
os.environ["MONGO_DB"] = os.getenv("TEST_MONGO_DB", "voicechat_test")
os.environ["REDIS_URL"] = os.getenv("TEST_REDIS_URL", "redis://redis:6379/1")
os.environ["RATE_LIMIT_STORAGE_URI"] = os.getenv(
    "TEST_RATE_LIMIT_STORAGE_URI", "async+memory://"
)

from pymongo import AsyncMongoClient

from app.core.config import settings
from app.core.rate_limit.limiter import rate_limiter
from app.db.init import init_database
from app.db.mongo import connect_mongo, disconnect_mongo
from app.factory import create_app
from app.socket import create_socket_server, register_socket_events

import pytest_asyncio
from asgi_lifespan import LifespanManager
from httpx import AsyncClient, ASGITransport
from redis.asyncio import Redis

TEST_SERVER_URL = os.getenv("TEST_SERVER_URL", "http://api_test:8000")
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/1")


def _assert_test_database() -> None:
    if "test" not in settings.mongo_db.lower():
        raise RuntimeError(
            "Integration tests refuse to clean non-test Mongo database "
            f"{settings.mongo_db!r}. Use MONGO_DB=voicechat_test or "
            "TEST_MONGO_DB=<test database>."
        )


@pytest_asyncio.fixture(scope="function", autouse=True)
async def app_lifecycle():
    _assert_test_database()
    await connect_mongo()
    await init_database()
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
    "message_receipts",
    "refresh_tokens",
    "verification_codes",
    "passkeys",
    "passkey_challenges",
    "discovery_tokens",
    "conversations",
    "conversation_participants",
    "channels",
    "devices",
    "device_prekeys",
    "spaces",
    "space_members",
    "invite_links",
    "join_requests",
    "relationships",
    "blocks",
    "push_tokens",
    "saved_messages",
    "notifications",
    "bots",
    "webhooks",
    "reports",
    "audit_logs",
    "slash_commands",
]


@pytest_asyncio.fixture(autouse=True)
async def clean_db():
    _assert_test_database()
    client: AsyncMongoClient = AsyncMongoClient(settings.mongo_uri)
    db = client[settings.mongo_db]

    for name in TEST_COLLECTIONS:
        await db[name].delete_many({})

    yield

    for name in TEST_COLLECTIONS:
        await db[name].delete_many({})

    await client.close()


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
