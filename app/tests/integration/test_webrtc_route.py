from __future__ import annotations

from datetime import UTC, datetime

import pytest
from bson import ObjectId

from app.core.config import settings
from app.core.security import create_access_token
from app.db.mongo import get_db


@pytest.mark.asyncio
async def test_get_ice_servers_returns_stun_and_coturn(inprocess_client, monkeypatch):
    db = get_db()
    user = {
        "_id": ObjectId(),
        "email": "webrtc-route@test.com",
        "username": "webrtc_route_user",
        "display_name": None,
        "bio": None,
        "avatar": None,
        "is_private": False,
        "default_discovery_enabled": True,
        "last_seen_at": None,
        "username_updated_at": None,
        "is_verified": True,
        "created_at": datetime.now(UTC),
        "updated_at": datetime.now(UTC),
    }
    await db["users"].insert_one(user)

    monkeypatch.setattr(settings, "call_stun_urls", ["stun:stun.example.com:19302"])
    monkeypatch.setattr(settings, "turn_provider", "coturn")
    monkeypatch.setattr(settings, "turn_multi", False)
    monkeypatch.setattr(settings, "coturn_urls", ["turn:coturn.example.com:3478"])
    monkeypatch.setattr(settings, "coturn_username", "coturn-user")
    monkeypatch.setattr(settings, "coturn_password", "coturn-pass")

    token = create_access_token(subject=str(user["_id"]))
    response = await inprocess_client.get(
        "/webrtc/ice-servers",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200, response.text
    payload = response.json()["data"]["ice_servers"]
    assert payload[0]["urls"] == "stun:stun.example.com:19302"
    assert payload[1]["urls"] == "turn:coturn.example.com:3478"
    assert payload[1]["username"] == "coturn-user"
