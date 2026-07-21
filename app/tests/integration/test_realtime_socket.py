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
from app.modules.pings.repository import pair_id_for

TEST_SERVER_URL = os.getenv("TEST_SERVER_URL", "http://api_test:8000")


async def _create_verified_user_and_tokens(email: str) -> tuple[dict, dict]:
    db = get_db()

    user = {
        "_id": ObjectId(),
        "email": email.lower(),
        "username": f"test_{uuid.uuid4().hex[::6]}",
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
    db = get_db()
    now = datetime.now(UTC)
    await db["pings"].insert_one(
        {
            "pair_id": pair_id_for(user_a_id, user_b_id),
            "from_user_id": user_a_id,
            "to_user_id": user_b_id,
            "status": "accepted",
            "created_at": now,
            "updated_at": now,
            "responded_at": now,
        }
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
