from __future__ import annotations

import asyncio
import os
from datetime import datetime, UTC
from io import BytesIO

from bson import ObjectId

import pytest
import socketio

from app.core.security import create_access_token
from app.db.mongo import get_db

TEST_SERVER_URL = os.getenv("TEST_SERVER_URL", "http://api_test:8000")


async def _create_verified_user_and_tokens(email: str) -> tuple[dict, dict]:
    db = get_db()

    user = {
        "_id": ObjectId(),
        "email": email.lower(),
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


@pytest.mark.asyncio
async def test_socket_connect_and_voice_delivery(live_client):
    try:
        health = await live_client.get("/health/live")
    except Exception:
        pytest.skip("Live server is not running on http://api_test:8000")

    assert health.status_code == 200

    sender, sender_tokens = await _create_verified_user_and_tokens("sender@test.com")
    receiver, receiver_tokens = await _create_verified_user_and_tokens("receiver@test.com")

    sender_id = str(sender["_id"])
    receiver_id = str(receiver["_id"])

    sender_sio = socketio.AsyncClient()
    receiver_sio = socketio.AsyncClient()

    receiver_events: list[dict] = []
    sender_status_events: list[dict] = []

    receiver_msg_event = asyncio.Event()
    sender_status_event = asyncio.Event()

    @receiver_sio.on("receive_voice_message")
    async def on_receive_voice_message(data):
        receiver_events.append(data)
        receiver_msg_event.set()

    @sender_sio.on("voice_message_status")
    async def on_voice_message_status(data):
        sender_status_events.append(data)
        sender_status_event.set()

    try:
        await asyncio.wait_for(
            sender_sio.connect(
                TEST_SERVER_URL,
                socketio_path="socket.io",
                auth={"token": sender_tokens["access_token"]},
                transports=["websocket"],
                wait_timeout=5,
            ),
            timeout=8,
        )

        await asyncio.wait_for(
            receiver_sio.connect(
                TEST_SERVER_URL,
                socketio_path="socket.io",
                auth={"token": receiver_tokens["access_token"]},
                transports=["websocket"],
                wait_timeout=5,
            ),
            timeout=8,
        )

        assert sender_sio.connected is True
        assert receiver_sio.connected is True

        files = {
            "file": ("sample.mp3", BytesIO(b"fake-audio"), "audio/mpeg"),
        }
        data = {
            "receiver_id": receiver_id,
            "duration_ms": "1000",
        }
        headers = {"Authorization": f"Bearer {sender_tokens['access_token']}"}

        upload_res = await live_client.post(
            "/messages/voice",
            headers=headers,
            files=files,
            data=data,
        )
        assert upload_res.status_code == 201

        uploaded_message = upload_res.json()["data"]
        message_id = uploaded_message["id"]

        await asyncio.wait_for(receiver_msg_event.wait(), timeout=5)

        assert len(receiver_events) == 1
        message = receiver_events[0].get("message", receiver_events[0])
        assert message["id"] == message_id
        assert message["sender_id"] == sender_id
        assert message["receiver_id"] == receiver_id

        await receiver_sio.emit("voice_message_delivered", {"message_id": message_id})

        await asyncio.wait_for(sender_status_event.wait(), timeout=5)

        assert len(sender_status_events) >= 1
        status_payload = sender_status_events[-1]
        assert status_payload["message_id"] == message_id
        assert status_payload["status"] == "delivered"

    finally:
        if sender_sio.connected:
            await asyncio.wait_for(sender_sio.disconnect(), timeout=3)
        if receiver_sio.connected:
            await asyncio.wait_for(receiver_sio.disconnect(), timeout=3)
        await asyncio.sleep(0.1)


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