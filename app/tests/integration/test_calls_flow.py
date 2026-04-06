from __future__ import annotations

import asyncio
import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest
import socketio
from bson import ObjectId

from app.core.config import settings
from app.core.security import create_access_token
from app.db.mongo import get_db
from app.modules.pings.repository import pair_id_for

TEST_SERVER_URL = os.getenv("TEST_SERVER_URL", "http://api_test:8000")


async def _create_verified_user_and_tokens(email: str) -> tuple[dict, dict]:
    db = get_db()
    now = datetime.now(UTC)

    user = {
        "_id": ObjectId(),
        "email": email.lower(),
        "username": f"call_{uuid.uuid4().hex[::6]}",
        "display_name": None,
        "bio": None,
        "avatar": None,
        "is_private": False,
        "default_discovery_enabled": True,
        "last_seen_at": None,
        "username_updated_at": None,
        "is_verified": True,
        "created_at": now,
        "updated_at": now,
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
    return sio


def _auth_header(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_call_accept_offer_answer_connected_and_end_flow(live_client):
    try:
        health = await live_client.get("/health/live")
    except Exception:
        pytest.skip("Live server is not running on http://api_test:8000")

    assert health.status_code == 200

    caller, caller_tokens = await _create_verified_user_and_tokens("caller-calls@test.com")
    callee, callee_tokens = await _create_verified_user_and_tokens("callee-calls@test.com")
    caller_id = str(caller["_id"])
    callee_id = str(callee["_id"])
    await _grant_chat_permission(caller_id, callee_id)

    caller_sio = socketio.AsyncClient()
    callee_sio = socketio.AsyncClient()

    caller_errors: list[dict] = []
    callee_errors: list[dict] = []
    incoming_events: list[dict] = []
    accepted_events: list[dict] = []
    offer_events: list[dict] = []
    answer_events: list[dict] = []
    connected_events: list[dict] = []
    ended_events: list[dict] = []
    caller_messages: list[dict] = []
    callee_messages: list[dict] = []

    incoming_event = asyncio.Event()
    accepted_event = asyncio.Event()
    offer_event = asyncio.Event()
    answer_event = asyncio.Event()
    connected_event = asyncio.Event()
    ended_event = asyncio.Event()
    caller_message_event = asyncio.Event()
    callee_message_event = asyncio.Event()

    @caller_sio.on("error")
    async def on_caller_error(data):
        caller_errors.append(data)

    @callee_sio.on("error")
    async def on_callee_error(data):
        callee_errors.append(data)

    @callee_sio.on("call.incoming")
    async def on_incoming(data):
        incoming_events.append(data)
        incoming_event.set()

    @caller_sio.on("call.accepted")
    async def on_accepted(data):
        accepted_events.append(data)
        accepted_event.set()

    @callee_sio.on("call.offer")
    async def on_offer(data):
        offer_events.append(data)
        offer_event.set()

    @caller_sio.on("call.answer")
    async def on_answer(data):
        answer_events.append(data)
        answer_event.set()

    @caller_sio.on("call.connected")
    @callee_sio.on("call.connected")
    async def on_connected(data):
        connected_events.append(data)
        connected_event.set()

    @caller_sio.on("call.ended")
    @callee_sio.on("call.ended")
    async def on_ended(data):
        ended_events.append(data)
        ended_event.set()

    @caller_sio.on("receive_message")
    async def on_caller_message(data):
        caller_messages.append(data)
        caller_message_event.set()

    @callee_sio.on("receive_message")
    async def on_callee_message(data):
        callee_messages.append(data)
        callee_message_event.set()

    try:
        await asyncio.wait_for(
            caller_sio.connect(
                TEST_SERVER_URL,
                socketio_path="socket.io",
                auth={"token": caller_tokens["access_token"]},
                transports=["websocket"],
                wait_timeout=5,
            ),
            timeout=8,
        )
        await asyncio.wait_for(
            callee_sio.connect(
                TEST_SERVER_URL,
                socketio_path="socket.io",
                auth={"token": callee_tokens["access_token"]},
                transports=["websocket"],
                wait_timeout=5,
            ),
            timeout=8,
        )

        create_res = await live_client.post(
            "/calls",
            headers=_auth_header(caller_tokens["access_token"]),
            json={"callee_user_id": callee_id, "type": "audio"},
        )
        assert create_res.status_code == 201, create_res.text
        create_payload = create_res.json()["data"]
        call_id = create_payload["call"]["id"]
        assert create_payload["call"]["status"] == "ringing"

        await asyncio.wait_for(incoming_event.wait(), timeout=5)
        assert incoming_events[-1]["call"]["id"] == call_id
        assert incoming_events[-1]["call"]["status"] == "ringing"

        accept_res = await live_client.post(
            f"/calls/{call_id}/accept",
            headers=_auth_header(callee_tokens["access_token"]),
            json={"socket_id": callee_sio.get_sid("/")},
        )
        assert accept_res.status_code == 200, accept_res.text
        assert accept_res.json()["data"]["call"]["status"] == "accepted"

        await asyncio.wait_for(accepted_event.wait(), timeout=5)
        assert accepted_events[-1]["call"]["id"] == call_id
        assert accepted_events[-1]["call"]["status"] == "accepted"

        await caller_sio.emit(
            "call.offer",
            {"call_id": call_id, "sdp": {"type": "offer", "sdp": "fake-offer"}},
        )
        await asyncio.wait_for(offer_event.wait(), timeout=5)
        assert offer_events[-1]["call_id"] == call_id
        assert offer_events[-1]["sdp"]["type"] == "offer"

        await callee_sio.emit(
            "call.answer",
            {"call_id": call_id, "sdp": {"type": "answer", "sdp": "fake-answer"}},
        )
        await asyncio.wait_for(answer_event.wait(), timeout=5)
        assert answer_events[-1]["call_id"] == call_id
        assert answer_events[-1]["sdp"]["type"] == "answer"

        await caller_sio.emit("call.connected", {"call_id": call_id})
        await asyncio.wait_for(connected_event.wait(), timeout=5)
        assert connected_events[-1]["call"]["status"] == "active"

        await caller_sio.emit("call.hangup", {"call_id": call_id})
        await asyncio.wait_for(ended_event.wait(), timeout=5)
        assert ended_events[-1]["call"]["status"] == "ended"
        await asyncio.wait_for(caller_message_event.wait(), timeout=5)
        await asyncio.wait_for(callee_message_event.wait(), timeout=5)

        caller_message = caller_messages[-1]
        callee_message = callee_messages[-1]
        assert caller_message["type"] == "call"
        assert callee_message["type"] == "call"
        assert caller_message["call"]["call_id"] == call_id
        assert callee_message["call"]["status"] == "ended"

        history_res = await live_client.get(
            f"/calls/history?peer_user_id={callee_id}&limit=10",
            headers=_auth_header(caller_tokens["access_token"]),
        )
        assert history_res.status_code == 200, history_res.text
        history_item = history_res.json()["data"][0]
        assert history_item["id"] == call_id
        assert history_item["status"] == "ended"
        assert history_item["message_id"] == caller_message["id"]

        message_history_res = await live_client.get(
            f"/messages/conversations/{callee_id}?limit=10",
            headers=_auth_header(caller_tokens["access_token"]),
        )
        assert message_history_res.status_code == 200, message_history_res.text
        message_item = message_history_res.json()["data"][0]
        assert message_item["type"] == "call"
        assert message_item["call"]["call_id"] == call_id

        conversations_res = await live_client.get(
            "/messages/conversations?limit=10",
            headers=_auth_header(caller_tokens["access_token"]),
        )
        assert conversations_res.status_code == 200, conversations_res.text
        conversation_item = conversations_res.json()["data"][0]
        assert conversation_item["peer_user"]["id"] == callee_id
        assert conversation_item["last_message"]["type"] == "call"
        assert conversation_item["last_message"]["call"]["call_id"] == call_id

        assert caller_errors == []
        assert callee_errors == []
    finally:
        if caller_sio.connected:
            await asyncio.wait_for(caller_sio.disconnect(), timeout=3)
        if callee_sio.connected:
            await asyncio.wait_for(callee_sio.disconnect(), timeout=3)
        await asyncio.sleep(0.1)


@pytest.mark.asyncio
async def test_call_recovery_after_socket_refresh(live_client):
    try:
        health = await live_client.get("/health/live")
    except Exception:
        pytest.skip("Live server is not running on http://api_test:8000")

    assert health.status_code == 200

    caller, caller_tokens = await _create_verified_user_and_tokens("caller-recovery@test.com")
    callee, callee_tokens = await _create_verified_user_and_tokens("callee-recovery@test.com")
    caller_id = str(caller["_id"])
    callee_id = str(callee["_id"])
    await _grant_chat_permission(caller_id, callee_id)

    caller_sio = await _connect_socket(caller_tokens["access_token"])
    callee_sio = await _connect_socket(callee_tokens["access_token"])
    refreshed_callee_sio: socketio.AsyncClient | None = None

    reconnecting_events: list[dict] = []
    recovery_events: list[dict] = []
    resumed_events: list[dict] = []
    offer_events: list[dict] = []
    answer_events: list[dict] = []
    connected_events: list[dict] = []

    reconnecting_event = asyncio.Event()
    recovery_event = asyncio.Event()
    resumed_event = asyncio.Event()
    offer_event = asyncio.Event()
    answer_event = asyncio.Event()
    connected_event = asyncio.Event()

    @caller_sio.on("call.reconnecting")
    async def on_reconnecting(data):
        reconnecting_events.append(data)
        reconnecting_event.set()

    @caller_sio.on("call.resumed")
    async def on_resumed(data):
        resumed_events.append(data)
        resumed_event.set()

    @caller_sio.on("call.answer")
    async def on_answer(data):
        answer_events.append(data)
        answer_event.set()

    @callee_sio.on("call.offer")
    async def on_offer(data):
        offer_events.append(data)
        offer_event.set()

    @caller_sio.on("call.connected")
    async def on_connected(data):
        connected_events.append(data)
        connected_event.set()

    try:
        create_res = await live_client.post(
            "/calls",
            headers=_auth_header(caller_tokens["access_token"]),
            json={"callee_user_id": callee_id, "type": "audio"},
        )
        assert create_res.status_code == 201, create_res.text
        call_id = create_res.json()["data"]["call"]["id"]

        accept_res = await live_client.post(
            f"/calls/{call_id}/accept",
            headers=_auth_header(callee_tokens["access_token"]),
            json={"socket_id": callee_sio.get_sid("/")},
        )
        assert accept_res.status_code == 200, accept_res.text

        await caller_sio.emit(
            "call.offer",
            {"call_id": call_id, "sdp": {"type": "offer", "sdp": "initial-offer"}},
        )
        await asyncio.wait_for(offer_event.wait(), timeout=5)
        assert offer_events[-1]["call_id"] == call_id

        await callee_sio.emit(
            "call.answer",
            {"call_id": call_id, "sdp": {"type": "answer", "sdp": "initial-answer"}},
        )
        await asyncio.wait_for(answer_event.wait(), timeout=5)
        assert answer_events[-1]["call_id"] == call_id

        await caller_sio.emit("call.connected", {"call_id": call_id})
        await asyncio.wait_for(connected_event.wait(), timeout=5)
        assert connected_events[-1]["call"]["status"] == "active"

        connected_event.clear()
        await asyncio.wait_for(callee_sio.disconnect(), timeout=3)
        await asyncio.wait_for(reconnecting_event.wait(), timeout=5)
        assert reconnecting_events[-1]["call"]["status"] == "reconnecting"

        refreshed_callee_sio = socketio.AsyncClient()

        @refreshed_callee_sio.on("call.recovery_available")
        async def on_recovery_available(data):
            recovery_events.append(data)
            recovery_event.set()

        @refreshed_callee_sio.on("call.offer")
        async def on_offer(data):
            offer_events.append(data)
            offer_event.set()

        await asyncio.wait_for(
            refreshed_callee_sio.connect(
                TEST_SERVER_URL,
                socketio_path="socket.io",
                auth={"token": callee_tokens["access_token"]},
                transports=["websocket"],
                wait_timeout=5,
            ),
            timeout=8,
        )

        await asyncio.wait_for(recovery_event.wait(), timeout=5)
        assert recovery_events[-1]["call"]["id"] == call_id
        assert recovery_events[-1]["call"]["status"] == "reconnecting"

        active_res = await live_client.get(
            "/calls/active",
            headers=_auth_header(callee_tokens["access_token"]),
        )
        assert active_res.status_code == 200, active_res.text
        assert active_res.json()["data"]["call"]["id"] == call_id

        await refreshed_callee_sio.emit("call.resume", {"call_id": call_id})
        await asyncio.wait_for(resumed_event.wait(), timeout=5)
        assert resumed_events[-1]["call"]["id"] == call_id

        offer_event.clear()
        await caller_sio.emit(
            "call.offer",
            {"call_id": call_id, "sdp": {"type": "offer", "sdp": "resumed-offer"}},
        )
        await asyncio.wait_for(offer_event.wait(), timeout=5)
        assert offer_events[-1]["call_id"] == call_id

        answer_event.clear()
        await refreshed_callee_sio.emit(
            "call.answer",
            {"call_id": call_id, "sdp": {"type": "answer", "sdp": "resumed-answer"}},
        )
        await asyncio.wait_for(answer_event.wait(), timeout=5)
        assert answer_events[-1]["call_id"] == call_id

        await caller_sio.emit("call.connected", {"call_id": call_id})
        await asyncio.wait_for(connected_event.wait(), timeout=5)
        assert connected_events[-1]["call"]["status"] == "active"
    finally:
        for sio in (caller_sio, callee_sio, refreshed_callee_sio):
            if sio is not None and sio.connected:
                await asyncio.wait_for(sio.disconnect(), timeout=3)
        await asyncio.sleep(0.1)


@pytest.mark.asyncio
async def test_call_reject_flow_emits_rejected(live_client):
    try:
        health = await live_client.get("/health/live")
    except Exception:
        pytest.skip("Live server is not running on http://api_test:8000")

    assert health.status_code == 200

    caller, caller_tokens = await _create_verified_user_and_tokens("caller-reject@test.com")
    callee, callee_tokens = await _create_verified_user_and_tokens("callee-reject@test.com")
    caller_id = str(caller["_id"])
    callee_id = str(callee["_id"])
    await _grant_chat_permission(caller_id, callee_id)

    caller_sio = await _connect_socket(caller_tokens["access_token"])
    callee_sio = await _connect_socket(callee_tokens["access_token"])

    rejected_events: list[dict] = []
    caller_messages: list[dict] = []
    callee_messages: list[dict] = []
    rejected_event = asyncio.Event()
    caller_message_event = asyncio.Event()
    callee_message_event = asyncio.Event()

    @caller_sio.on("call.rejected")
    async def on_rejected(data):
        rejected_events.append(data)
        rejected_event.set()

    @caller_sio.on("receive_message")
    async def on_caller_message(data):
        caller_messages.append(data)
        caller_message_event.set()

    @callee_sio.on("receive_message")
    async def on_callee_message(data):
        callee_messages.append(data)
        callee_message_event.set()

    try:
        create_res = await live_client.post(
            "/calls",
            headers=_auth_header(caller_tokens["access_token"]),
            json={"callee_user_id": callee_id, "type": "video"},
        )
        assert create_res.status_code == 201, create_res.text
        call_id = create_res.json()["data"]["call"]["id"]

        reject_res = await live_client.post(
            f"/calls/{call_id}/reject",
            headers=_auth_header(callee_tokens["access_token"]),
        )
        assert reject_res.status_code == 200, reject_res.text
        assert reject_res.json()["data"]["status"] == "rejected"

        await asyncio.wait_for(rejected_event.wait(), timeout=5)
        assert rejected_events[-1]["call"]["id"] == call_id
        assert rejected_events[-1]["call"]["status"] == "rejected"
        await asyncio.wait_for(caller_message_event.wait(), timeout=5)
        await asyncio.wait_for(callee_message_event.wait(), timeout=5)
        assert caller_messages[-1]["type"] == "call"
        assert caller_messages[-1]["call"]["status"] == "rejected"
        assert callee_messages[-1]["call"]["call_id"] == call_id

        history_res = await live_client.get(
            f"/calls/history?peer_user_id={callee_id}&limit=10",
            headers=_auth_header(caller_tokens["access_token"]),
        )
        assert history_res.status_code == 200, history_res.text
        history_item = history_res.json()["data"][0]
        assert history_item["status"] == "rejected"
        assert history_item["message_id"] == caller_messages[-1]["id"]

        message_history_res = await live_client.get(
            f"/messages/conversations/{callee_id}?limit=10",
            headers=_auth_header(caller_tokens["access_token"]),
        )
        assert message_history_res.status_code == 200, message_history_res.text
        assert message_history_res.json()["data"][0]["call"]["status"] == "rejected"
    finally:
        if caller_sio.connected:
            await asyncio.wait_for(caller_sio.disconnect(), timeout=3)
        if callee_sio.connected:
            await asyncio.wait_for(callee_sio.disconnect(), timeout=3)
        await asyncio.sleep(0.1)


@pytest.mark.asyncio
async def test_multi_device_accept_only_routes_offer_to_accepted_socket(live_client):
    try:
        health = await live_client.get("/health/live")
    except Exception:
        pytest.skip("Live server is not running on http://api_test:8000")

    assert health.status_code == 200

    caller, caller_tokens = await _create_verified_user_and_tokens("caller-multi@test.com")
    callee, callee_tokens = await _create_verified_user_and_tokens("callee-multi@test.com")
    caller_id = str(caller["_id"])
    callee_id = str(callee["_id"])
    await _grant_chat_permission(caller_id, callee_id)

    caller_sio = await _connect_socket(caller_tokens["access_token"])
    callee_primary_sio = await _connect_socket(callee_tokens["access_token"])
    callee_secondary_sio = await _connect_socket(callee_tokens["access_token"])

    primary_incoming = asyncio.Event()
    secondary_incoming = asyncio.Event()
    primary_offer = asyncio.Event()

    primary_offer_events: list[dict] = []
    secondary_offer_events: list[dict] = []

    @callee_primary_sio.on("call.incoming")
    async def on_primary_incoming(_data):
        primary_incoming.set()

    @callee_secondary_sio.on("call.incoming")
    async def on_secondary_incoming(_data):
        secondary_incoming.set()

    @callee_primary_sio.on("call.offer")
    async def on_primary_offer(data):
        primary_offer_events.append(data)
        primary_offer.set()

    @callee_secondary_sio.on("call.offer")
    async def on_secondary_offer(data):
        secondary_offer_events.append(data)

    try:
        create_res = await live_client.post(
            "/calls",
            headers=_auth_header(caller_tokens["access_token"]),
            json={"callee_user_id": callee_id, "type": "audio"},
        )
        assert create_res.status_code == 201, create_res.text
        call_id = create_res.json()["data"]["call"]["id"]

        await asyncio.wait_for(primary_incoming.wait(), timeout=5)
        await asyncio.wait_for(secondary_incoming.wait(), timeout=5)

        accept_res = await live_client.post(
            f"/calls/{call_id}/accept",
            headers=_auth_header(callee_tokens["access_token"]),
            json={"socket_id": callee_primary_sio.get_sid("/")},
        )
        assert accept_res.status_code == 200, accept_res.text

        await caller_sio.emit(
            "call.offer",
            {"call_id": call_id, "sdp": {"type": "offer", "sdp": "room-bound-offer"}},
        )
        await asyncio.wait_for(primary_offer.wait(), timeout=5)
        await asyncio.sleep(0.5)

        assert len(primary_offer_events) == 1
        assert primary_offer_events[0]["call_id"] == call_id
        assert secondary_offer_events == []

        end_res = await live_client.post(
            f"/calls/{call_id}/end",
            headers=_auth_header(caller_tokens["access_token"]),
        )
        assert end_res.status_code == 200, end_res.text
    finally:
        for sio in (caller_sio, callee_primary_sio, callee_secondary_sio):
            if sio.connected:
                await asyncio.wait_for(sio.disconnect(), timeout=3)
        await asyncio.sleep(0.1)


@pytest.mark.asyncio
async def test_second_live_call_returns_busy_conflict(live_client):
    try:
        health = await live_client.get("/health/live")
    except Exception:
        pytest.skip("Live server is not running on http://api_test:8000")

    assert health.status_code == 200

    caller_a, caller_a_tokens = await _create_verified_user_and_tokens("caller-a@test.com")
    caller_b, caller_b_tokens = await _create_verified_user_and_tokens("caller-b@test.com")
    callee, _ = await _create_verified_user_and_tokens("callee-busy@test.com")

    caller_a_id = str(caller_a["_id"])
    caller_b_id = str(caller_b["_id"])
    callee_id = str(callee["_id"])

    await _grant_chat_permission(caller_a_id, callee_id)
    await _grant_chat_permission(caller_b_id, callee_id)

    first_res = await live_client.post(
        "/calls",
        headers=_auth_header(caller_a_tokens["access_token"]),
        json={"callee_user_id": callee_id, "type": "audio"},
    )
    assert first_res.status_code == 201, first_res.text
    first_call_id = first_res.json()["data"]["call"]["id"]

    second_res = await live_client.post(
        "/calls",
        headers=_auth_header(caller_b_tokens["access_token"]),
        json={"callee_user_id": callee_id, "type": "audio"},
    )
    assert second_res.status_code == 409, second_res.text
    assert second_res.json()["error"]["code"] == "CALL_BUSY"

    cleanup_res = await live_client.post(
        f"/calls/{first_call_id}/end",
        headers=_auth_header(caller_a_tokens["access_token"]),
    )
    assert cleanup_res.status_code == 200, cleanup_res.text


@pytest.mark.asyncio
async def test_ringing_call_cancel_creates_call_message_and_history(inprocess_client):
    caller, caller_tokens = await _create_verified_user_and_tokens("caller-cancel@test.com")
    callee, _ = await _create_verified_user_and_tokens("callee-cancel@test.com")
    caller_id = str(caller["_id"])
    callee_id = str(callee["_id"])
    await _grant_chat_permission(caller_id, callee_id)

    create_res = await inprocess_client.post(
        "/calls",
        headers=_auth_header(caller_tokens["access_token"]),
        json={"callee_user_id": callee_id, "type": "audio"},
    )
    assert create_res.status_code == 201, create_res.text
    call_id = create_res.json()["data"]["call"]["id"]

    cancel_res = await inprocess_client.post(
        f"/calls/{call_id}/end",
        headers=_auth_header(caller_tokens["access_token"]),
    )
    assert cancel_res.status_code == 200, cancel_res.text
    assert cancel_res.json()["data"]["status"] == "cancelled"

    history_res = await inprocess_client.get(
        f"/calls/history?peer_user_id={callee_id}&limit=10",
        headers=_auth_header(caller_tokens["access_token"]),
    )
    assert history_res.status_code == 200, history_res.text
    history_item = history_res.json()["data"][0]
    assert history_item["status"] == "cancelled"
    assert history_item["message_id"] is not None

    message_history_res = await inprocess_client.get(
        f"/messages/conversations/{callee_id}?limit=10",
        headers=_auth_header(caller_tokens["access_token"]),
    )
    assert message_history_res.status_code == 200, message_history_res.text
    message_item = message_history_res.json()["data"][0]
    assert message_item["type"] == "call"
    assert message_item["call"]["status"] == "cancelled"


@pytest.mark.asyncio
async def test_expired_call_creates_call_message_and_history(inprocess_client, monkeypatch):
    monkeypatch.setattr(settings, "call_ring_timeout_seconds", 0)

    caller, caller_tokens = await _create_verified_user_and_tokens("caller-expired@test.com")
    callee, _ = await _create_verified_user_and_tokens("callee-expired@test.com")
    caller_id = str(caller["_id"])
    callee_id = str(callee["_id"])
    await _grant_chat_permission(caller_id, callee_id)

    create_res = await inprocess_client.post(
        "/calls",
        headers=_auth_header(caller_tokens["access_token"]),
        json={"callee_user_id": callee_id, "type": "video"},
    )
    assert create_res.status_code == 201, create_res.text
    call_id = create_res.json()["data"]["call"]["id"]

    await asyncio.sleep(0.05)

    history_res = await inprocess_client.get(
        f"/calls/history?peer_user_id={callee_id}&limit=10",
        headers=_auth_header(caller_tokens["access_token"]),
    )
    assert history_res.status_code == 200, history_res.text
    history_item = history_res.json()["data"][0]
    assert history_item["id"] == call_id
    assert history_item["status"] == "expired"
    assert history_item["message_id"] is not None

    message_history_res = await inprocess_client.get(
        f"/messages/conversations/{callee_id}?limit=10",
        headers=_auth_header(caller_tokens["access_token"]),
    )
    assert message_history_res.status_code == 200, message_history_res.text
    message_item = message_history_res.json()["data"][0]
    assert message_item["type"] == "call"
    assert message_item["call"]["status"] == "expired"


@pytest.mark.asyncio
async def test_old_terminal_call_appears_in_history_without_message_id(inprocess_client):
    caller, caller_tokens = await _create_verified_user_and_tokens("caller-old-history@test.com")
    callee, _ = await _create_verified_user_and_tokens("callee-old-history@test.com")

    call_id = ObjectId()
    now = datetime.now(UTC)
    db = get_db()
    await db["calls"].insert_one(
        {
            "_id": call_id,
            "caller_user_id": str(caller["_id"]),
            "callee_user_id": str(callee["_id"]),
            "participant_user_ids": [str(caller["_id"]), str(callee["_id"])],
            "type": "audio",
            "status": "ended",
            "room_id": f"call:{call_id}",
            "created_at": now - timedelta(minutes=5),
            "updated_at": now,
            "answered_at": now - timedelta(minutes=4),
            "ended_at": now,
            "expires_at": None,
            "reconnect_deadline_at": None,
            "disconnected_user_ids": [],
            "is_live": False,
            "history_message_id": None,
        }
    )

    history_res = await inprocess_client.get(
        f"/calls/history?peer_user_id={callee['_id']}&limit=10",
        headers=_auth_header(caller_tokens["access_token"]),
    )
    assert history_res.status_code == 200, history_res.text
    history_item = history_res.json()["data"][0]
    assert history_item["id"] == str(call_id)
    assert history_item["message_id"] is None
