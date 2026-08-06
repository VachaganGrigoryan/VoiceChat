from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
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
from app.db.models import RelationshipDocument
from app.modules.relationships.dependencies import get_connection_service
from app.modules.relationships.router import connections_router

FIXED_NOW = datetime(2026, 3, 24, 12, 0, 0, tzinfo=UTC)

# `get_current_user` resolves to a UserDocument in production; the passkeys
# router only reads `current_user.str_id`. These contract tests run DB-less
# (no init_beanie), so a lightweight stand-in exposing `str_id` is enough.
FAKE_CURRENT_USER = SimpleNamespace(str_id="user-1")


class FakeSio:
    async def emit(self, *args: Any, **kwargs: Any) -> None:
        return None


class FakeConnectionService:
    async def request(
        self, *, from_user_id: str, to_user_id: str
    ) -> RelationshipDocument:
        return RelationshipDocument.model_validate(
            {
                "_id": "507f1f77bcf86cd799439011",
                "kind": "connection",
                "user_id": from_user_id,
                "target_type": "user",
                "target_id": to_user_id,
                "status": "pending",
                "initiation": "request",
                "initiated_by": from_user_id,
                "pair_id": f"{from_user_id}_{to_user_id}",
                "requested_at": FIXED_NOW,
                "created_at": FIXED_NOW,
                "updated_at": FIXED_NOW,
            }
        )


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
                connection_status="none",
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
    app.include_router(connections_router)
    app.include_router(discovery_router)
    app.include_router(passkeys_router)
    app.state.sio = FakeSio()
    app.dependency_overrides[get_sio] = lambda: app.state.sio
    app.dependency_overrides[get_current_user_id] = lambda: "user-1"
    app.dependency_overrides[get_current_user] = lambda: FAKE_CURRENT_USER
    app.dependency_overrides[get_connection_service] = (
        lambda: FakeConnectionService()
    )
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
async def test_connection_create_response_uses_success_envelope(
    contract_client: AsyncClient,
):
    response = await contract_client.post(
        "/connections/user-2/ping",
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["success"] is True
    assert body["request_id"]
    assert body["data"]["user_id"] == "user-1"
    assert body["data"]["target_id"] == "user-2"
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
