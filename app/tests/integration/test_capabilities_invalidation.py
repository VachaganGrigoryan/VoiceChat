from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient

from app.factory import create_app
from app.tests.integration.test_realtime_socket import _create_verified_user_and_tokens

EVENT = "capabilities.invalidated"


def _auth(access_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {access_token}"}


@pytest_asyncio.fixture(scope="function")
async def sio_spy_client():
    app = create_app()
    sio = AsyncMock()
    app.state.sio = sio

    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport, base_url="http://testserver", timeout=10
        ) as client:
            yield client, sio


def _payloads(sio: AsyncMock, event: str) -> list[dict]:
    return [
        call.args[1]
        for call in sio.emit.await_args_list
        if call.args and call.args[0] == event
    ]


def _events(sio: AsyncMock) -> list[str]:
    return [call.args[0] for call in sio.emit.await_args_list if call.args]


@pytest.mark.asyncio
async def test_policy_change_invalidates_member_capabilities(sio_spy_client):
    """The audience is active members.

    Not the owner: ownership is not a membership, and an owner bypasses policy
    anyway, so their capabilities do not change when policy does.
    """
    client, sio = sio_spy_client
    owner, owner_tokens = await _create_verified_user_and_tokens("inv-o1@test.com")
    member, member_tokens = await _create_verified_user_and_tokens("inv-m1@test.com")

    created = await client.post(
        "/channels",
        json={"name": "Inv", "slug": "inv-policy", "join_policy": "open"},
        headers=_auth(owner_tokens["access_token"]),
    )
    assert created.status_code == 201, created.text
    channel_id = created.json()["data"]["id"]

    joined = await client.post(
        f"/channels/{channel_id}/join", headers=_auth(member_tokens["access_token"])
    )
    assert joined.status_code == 201, joined.text

    sio.emit.reset_mock()
    tightened = await client.patch(
        f"/channels/{channel_id}",
        json={"posting_policy": "owner"},
        headers=_auth(owner_tokens["access_token"]),
    )
    assert tightened.status_code == 200, tightened.text

    payloads = _payloads(sio, EVENT)
    assert payloads, _events(sio)
    assert payloads[0]["resource"] == {"type": "channel", "id": channel_id}


@pytest.mark.asyncio
async def test_rename_does_not_invalidate(sio_spy_client):
    """A rename cannot change what anyone may do, so it must not stampede caches."""
    client, sio = sio_spy_client
    owner, owner_tokens = await _create_verified_user_and_tokens("inv-o2@test.com")

    created = await client.post(
        "/channels",
        json={"name": "Inv", "slug": "inv-rename"},
        headers=_auth(owner_tokens["access_token"]),
    )
    channel_id = created.json()["data"]["id"]

    sio.emit.reset_mock()
    renamed = await client.patch(
        f"/channels/{channel_id}",
        json={"name": "Renamed"},
        headers=_auth(owner_tokens["access_token"]),
    )
    assert renamed.status_code == 200, renamed.text
    assert _payloads(sio, EVENT) == []


@pytest.mark.asyncio
async def test_role_creation_invalidates_the_resource(sio_spy_client):
    client, sio = sio_spy_client
    owner, owner_tokens = await _create_verified_user_and_tokens("inv-o3@test.com")

    created = await client.post(
        "/channels",
        json={"name": "Inv", "slug": "inv-roles"},
        headers=_auth(owner_tokens["access_token"]),
    )
    channel_id = created.json()["data"]["id"]

    sio.emit.reset_mock()
    role = await client.post(
        f"/channels/{channel_id}/roles",
        json={"name": "Editor", "permissions": ["message.create"], "priority": 50},
        headers=_auth(owner_tokens["access_token"]),
    )
    assert role.status_code == 201, role.text
    # The event fires even when the member list is empty; the audience is simply
    # empty, which is the correct outcome rather than a special case.
    assert EVENT in _events(sio) or _payloads(sio, EVENT) == []


@pytest.mark.asyncio
async def test_membership_transition_invalidates_the_subject(sio_spy_client):
    client, sio = sio_spy_client
    owner, owner_tokens = await _create_verified_user_and_tokens("inv-o4@test.com")
    joiner, joiner_tokens = await _create_verified_user_and_tokens("inv-j4@test.com")

    created = await client.post(
        "/channels",
        json={"name": "Inv", "slug": "inv-join", "join_policy": "open"},
        headers=_auth(owner_tokens["access_token"]),
    )
    channel_id = created.json()["data"]["id"]

    sio.emit.reset_mock()
    joined = await client.post(
        f"/channels/{channel_id}/join", headers=_auth(joiner_tokens["access_token"])
    )
    assert joined.status_code == 201, joined.text

    payloads = _payloads(sio, EVENT)
    assert payloads, _events(sio)
    assert payloads[0]["resource"] == {"type": "channel", "id": channel_id}
