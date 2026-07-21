from __future__ import annotations

import pytest

from app.tests.integration.test_realtime_socket import _create_verified_user_and_tokens


def _auth(access_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {access_token}"}


@pytest.mark.asyncio
async def test_register_device_and_prekey_bundle_consumes_one_time(inprocess_client):
    owner, owner_tokens = await _create_verified_user_and_tokens("dev-owner@test.com")
    requester, requester_tokens = await _create_verified_user_and_tokens(
        "dev-req@test.com"
    )

    register = await inprocess_client.post(
        "/devices",
        json={
            "device_id": "device-1",
            "name": "Pixel",
            "platform": "android",
            "identity_public_key": "IDKEY",
            "signing_public_key": "SIGNKEY",
            "registration_id": 42,
        },
        headers=_auth(owner_tokens["access_token"]),
    )
    assert register.status_code == 201, register.text
    assert register.json()["data"]["device_id"] == "device-1"

    upload = await inprocess_client.post(
        "/devices/device-1/prekeys",
        json={
            "prekeys": [
                {"key_id": 1, "public_key": "PK1", "one_time": True},
                {"key_id": 2, "public_key": "PK2", "one_time": True},
            ]
        },
        headers=_auth(owner_tokens["access_token"]),
    )
    assert upload.status_code == 201, upload.text
    assert upload.json()["data"]["uploaded"] == 2

    # First bundle request returns identity keys + one one-time prekey.
    first = await inprocess_client.get(
        f"/users/{owner['_id']}/prekey-bundle",
        headers=_auth(requester_tokens["access_token"]),
    )
    assert first.status_code == 200, first.text
    first_data = first.json()["data"]
    assert first_data["identity_public_key"] == "IDKEY"
    assert first_data["one_time_prekey"] is not None

    # Second request returns a different (or no) one-time prekey — first is consumed.
    second = await inprocess_client.get(
        f"/users/{owner['_id']}/prekey-bundle",
        headers=_auth(requester_tokens["access_token"]),
    )
    assert second.status_code == 200, second.text
    second_prekey = second.json()["data"]["one_time_prekey"]
    if second_prekey is not None:
        assert second_prekey["key_id"] != first_data["one_time_prekey"]["key_id"]

    # Third request: both one-time prekeys consumed → none left.
    third = await inprocess_client.get(
        f"/users/{owner['_id']}/prekey-bundle",
        headers=_auth(requester_tokens["access_token"]),
    )
    assert third.status_code == 200, third.text
    assert third.json()["data"]["one_time_prekey"] is None


@pytest.mark.asyncio
async def test_prekey_bundle_missing_device_is_404(inprocess_client):
    _, requester_tokens = await _create_verified_user_and_tokens("dev-req2@test.com")
    target, _ = await _create_verified_user_and_tokens("dev-target@test.com")

    resp = await inprocess_client.get(
        f"/users/{target['_id']}/prekey-bundle",
        headers=_auth(requester_tokens["access_token"]),
    )
    assert resp.status_code == 404, resp.text
    assert resp.json()["error"]["code"] == "DEVICE_NOT_FOUND"
