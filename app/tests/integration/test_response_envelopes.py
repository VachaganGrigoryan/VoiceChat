from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.core.deps import get_sio
from app.core.http import RequestIdMiddleware, SuccessEnvelopeMiddleware
from app.core.security import get_current_user, get_current_user_id
from app.modules.discovery.dependencies import get_discovery_service
from app.modules.discovery.router import router as discovery_router
from app.modules.discovery.schemas import DiscoveryUserSummary
from app.modules.passkeys.dependencies import get_passkey_service
from app.modules.passkeys.router import router as passkeys_router
from app.modules.passkeys.schemas import PasskeyResponse
from app.modules.pings.dependencies import get_pings_service
from app.modules.pings.router import router as pings_router
from app.modules.pings.schemas import PingResponse

FIXED_NOW = datetime(2026, 3, 24, 12, 0, 0, tzinfo=UTC)


class FakeSio:
    async def emit(self, *args: Any, **kwargs: Any) -> None:
        return None


class FakePingsRepo:
    async def find_by_id(self, ping_id: str) -> dict[str, Any]:
        return {"_id": ping_id}


class FakePingsService:
    def __init__(self) -> None:
        self.pings_repo = FakePingsRepo()

    async def send_ping(self, *, from_user_id: str, to_user_id: str) -> PingResponse:
        return PingResponse(
            id="ping-1",
            from_user_id=from_user_id,
            to_user_id=to_user_id,
            status="pending",
            created_at=FIXED_NOW,
            updated_at=FIXED_NOW,
            responded_at=None,
        )

    async def to_realtime_payload(
        self, doc: dict[str, Any], *, incoming_for: str
    ) -> dict[str, Any]:
        return {"id": doc["_id"], "incoming_for": incoming_for}


class FakeDiscoveryService:
    async def search_users(
        self,
        *,
        q: str,
        requester_user_id: str,
        limit: int = 20,
    ) -> list[DiscoveryUserSummary]:
        return [
            DiscoveryUserSummary(
                id="user-2",
                username=f"{q}_match",
                display_name="Target User",
                avatar=None,
                is_online=False,
                can_ping=True,
                chat_allowed=False,
                ping_status="none",
                discovered_via="username",
            )
        ]


class FakePasskeyService:
    async def start_registration(
        self, *, user_id: str, nickname: str | None = None
    ) -> dict[str, Any]:
        return {
            "challenge": "reg-challenge",
            "rp": {"id": "example.test", "name": "Example"},
            "nickname": nickname,
        }

    async def finish_registration(
        self,
        *,
        user_id: str,
        credential: dict[str, Any],
        nickname: str | None = None,
    ) -> PasskeyResponse:
        return PasskeyResponse(
            credential_id="cred-123",
            nickname=nickname,
            transports=["hybrid", "internal"],
            device_type="multi_device",
            backed_up=True,
            aaguid="aaguid-1",
            created_at=FIXED_NOW,
            last_used_at=None,
        )

    async def list_passkeys(self, *, user_id: str) -> list[PasskeyResponse]:
        return [
            PasskeyResponse(
                credential_id="cred-123",
                nickname="MacBook",
                transports=["hybrid", "internal"],
                device_type="multi_device",
                backed_up=True,
                aaguid="aaguid-1",
                created_at=FIXED_NOW,
                last_used_at=None,
            )
        ]

    async def delete_passkey(self, *, user_id: str, credential_id: str) -> bool:
        return credential_id == "cred-123"


@pytest_asyncio.fixture
async def contract_client():
    app = FastAPI()
    app.add_middleware(RequestIdMiddleware)
    app.add_middleware(SuccessEnvelopeMiddleware)
    app.include_router(pings_router)
    app.include_router(discovery_router)
    app.include_router(passkeys_router)
    app.state.sio = FakeSio()
    app.dependency_overrides[get_sio] = lambda: app.state.sio
    app.dependency_overrides[get_current_user_id] = lambda: "user-1"
    app.dependency_overrides[get_current_user] = lambda: {
        "_id": "user-1",
        "id": "user-1",
        "email": "user@example.com",
    }
    app.dependency_overrides[get_pings_service] = lambda: FakePingsService()
    app.dependency_overrides[get_discovery_service] = lambda: FakeDiscoveryService()
    app.dependency_overrides[get_passkey_service] = lambda: FakePasskeyService()

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://testserver",
        timeout=10,
    ) as client:
        yield client


@pytest_asyncio.fixture(autouse=True)
async def app_lifecycle():
    yield


@pytest_asyncio.fixture(autouse=True)
async def clean_redis():
    yield


@pytest_asyncio.fixture(autouse=True)
async def clean_db():
    yield


@pytest.mark.asyncio
async def test_pings_create_response_uses_success_envelope(
    contract_client: AsyncClient,
):
    response = await contract_client.post(
        "/pings",
        json={"to_user_id": "user-2"},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["success"] is True
    assert body["request_id"]
    assert body["data"]["from_user_id"] == "user-1"
    assert body["data"]["to_user_id"] == "user-2"
    assert "success" not in body["data"]


@pytest.mark.asyncio
async def test_discovery_search_response_uses_success_envelope(
    contract_client: AsyncClient,
):
    response = await contract_client.get("/discovery/users/search?q=disc&limit=5")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["success"] is True
    assert body["request_id"]
    assert isinstance(body["data"], list)
    assert body["data"][0]["id"] == "user-2"


@pytest.mark.asyncio
async def test_passkey_routes_use_success_envelope(contract_client: AsyncClient):
    start_response = await contract_client.post(
        "/auth/passkeys/register/start",
        json={"nickname": "MacBook"},
    )
    assert start_response.status_code == 200, start_response.text
    start_body = start_response.json()
    assert start_body["success"] is True
    assert start_body["request_id"]
    assert start_body["data"]["challenge"] == "reg-challenge"
    assert start_body["data"]["nickname"] == "MacBook"

    finish_response = await contract_client.post(
        "/auth/passkeys/register/finish",
        json={
            "nickname": "MacBook",
            "credential": {
                "id": "cred-id",
                "response": {"clientDataJSON": "ignored-for-route-test"},
            },
        },
    )
    assert finish_response.status_code == 200, finish_response.text
    finish_body = finish_response.json()
    assert finish_body["success"] is True
    assert finish_body["request_id"]
    assert finish_body["data"]["credential_id"] == "cred-123"
    assert "passkey" not in finish_body
    assert "success" not in finish_body["data"]

    list_response = await contract_client.get("/auth/passkeys")
    assert list_response.status_code == 200, list_response.text
    list_body = list_response.json()
    assert list_body["success"] is True
    assert list_body["request_id"]
    assert isinstance(list_body["data"], list)
    assert list_body["data"][0]["credential_id"] == "cred-123"

    delete_response = await contract_client.delete("/auth/passkeys/cred-123")
    assert delete_response.status_code == 200, delete_response.text
    delete_body = delete_response.json()
    assert delete_body["success"] is True
    assert delete_body["request_id"]
    assert delete_body["data"] == {"deleted": True}
    assert "success" not in delete_body["data"]
