from __future__ import annotations

import asyncio
import os
import uuid
from datetime import UTC, datetime

import pytest
import socketio
from bson import ObjectId

from app.core.security import create_access_token
from app.db.mongo import get_db
from app.modules.relationships.service import RelationshipService

TEST_SERVER_URL = os.getenv("TEST_SERVER_URL", "http://api_test:8000")


async def _create_verified_user_and_tokens(email: str) -> tuple[dict, dict]:
    db = get_db()

    user = {
        "_id": ObjectId(),
        "email": email.lower(),
        "username": f"test_{uuid.uuid4().hex[:12]}",
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

    access_token = create_access_token(subject=str(user["_id"]))

    return user, {
        "access_token": access_token,
        "token_type": "bearer",
    }


async def _grant_chat_permission(user_a_id: str, user_b_id: str) -> None:
    await RelationshipService().request(
        kind="connection",
        user_id=user_a_id,
        target_type="user",
        target_id=user_b_id,
        status="active",
    )


async def _connect_socket(access_token: str) -> socketio.AsyncClient:
    sio = socketio.AsyncClient()
    await asyncio.wait_for(
        sio.connect(
            TEST_SERVER_URL,
            socketio_path="socket.io",
            auth={"token": access_token},
            transports=["websocket"],
            wait_timeout=5,
        ),
        timeout=8,
    )
    assert sio.connected is True
    return sio


@pytest.mark.asyncio
async def test_socket_rejects_invalid_token():
    sio = socketio.AsyncClient()

    try:
        with pytest.raises(Exception):
            await asyncio.wait_for(
                sio.connect(
                    TEST_SERVER_URL,
                    socketio_path="socket.io",
                    auth={"token": "invalid-token"},
                    transports=["websocket"],
                    wait_timeout=3,
                ),
                timeout=5,
            )
    finally:
        if sio.connected:
            await asyncio.wait_for(sio.disconnect(), timeout=3)
        await asyncio.sleep(0.1)


@pytest.mark.asyncio
async def test_join_channel_room_delivers_message_and_rejects_non_readers(live_client):
    """A socket that joins a channel's room gets its messages live, and a
    viewer without read access is rejected rather than silently subscribed."""
    try:
        health = await live_client.get("/health/live")
    except Exception:
        pytest.skip("Live server is not running on http://api_test:8000")
    assert health.status_code == 200

    owner, owner_tokens = await _create_verified_user_and_tokens(
        "join-room-owner@test.com"
    )
    outsider, outsider_tokens = await _create_verified_user_and_tokens(
        "join-room-outsider@test.com"
    )

    created = await live_client.post(
        "/channels",
        headers={"Authorization": f"Bearer {owner_tokens['access_token']}"},
        json={
            "name": "Join Room",
            "kind": "text",
            "visibility": "private",
            "posting_policy": "everyone",
            "comment_policy": "everyone",
            "slug": f"join-room-{uuid.uuid4().hex[:8]}",
        },
    )
    assert created.status_code == 201, created.text
    channel_id = created.json()["data"]["id"]

    owner_sio = await _connect_socket(owner_tokens["access_token"])
    outsider_sio = await _connect_socket(outsider_tokens["access_token"])

    received: list[dict] = []
    received_event = asyncio.Event()
    outsider_errors: list[dict] = []
    outsider_error_event = asyncio.Event()

    @owner_sio.on("receive_message")
    async def on_receive_message(data):
        received.append(data)
        received_event.set()

    @outsider_sio.on("error")
    async def on_error(data):
        outsider_errors.append(data)
        outsider_error_event.set()

    try:
        await owner_sio.emit("join_channel", {"channel_id": channel_id})
        await outsider_sio.emit("join_channel", {"channel_id": channel_id})

        await asyncio.wait_for(outsider_error_event.wait(), timeout=5)
        assert outsider_errors[-1]["code"] == "FORBIDDEN"

        sent = await live_client.post(
            f"/messages/channel/{channel_id}/text",
            headers={"Authorization": f"Bearer {owner_tokens['access_token']}"},
            json={"text": "delivered over the channel room"},
        )
        assert sent.status_code == 201, sent.text

        await asyncio.wait_for(received_event.wait(), timeout=5)
        assert received[-1]["content"]["plaintext"]["text"] == (
            "delivered over the channel room"
        )
    finally:
        if owner_sio.connected:
            await asyncio.wait_for(owner_sio.disconnect(), timeout=3)
        if outsider_sio.connected:
            await asyncio.wait_for(outsider_sio.disconnect(), timeout=3)
        await asyncio.sleep(0.1)
