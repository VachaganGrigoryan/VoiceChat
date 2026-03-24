from __future__ import annotations

import asyncio
import os
import uuid
from datetime import UTC, datetime
from io import BytesIO

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


def _media_upload(
    *,
    kind: str,
    filename: str,
    content: bytes,
    mime: str,
    receiver_id: str,
    text: str | None = None,
    duration_ms: int | None = None,
) -> tuple[dict, dict]:
    files = {
        "file": (filename, BytesIO(content), mime),
    }
    message_type = "file" if kind == "file" else "media"
    data: dict[str, str] = {
        "type": message_type,
        "receiver_id": receiver_id,
    }
    if message_type == "media":
        data["media_kind"] = kind
    if text is not None:
        data["text"] = text
    if duration_ms is not None:
        data["duration_ms"] = str(duration_ms)
    return data, files


async def _post_media(
    live_client,
    *,
    access_token: str,
    kind: str,
    receiver_id: str,
    filename: str,
    content: bytes,
    mime: str,
    text: str | None = None,
    duration_ms: int | None = None,
):
    data, files = _media_upload(
        kind=kind,
        filename=filename,
        content=content,
        mime=mime,
        receiver_id=receiver_id,
        text=text,
        duration_ms=duration_ms,
    )
    headers = {"Authorization": f"Bearer {access_token}"}
    return await live_client.post(
        "/messages/media",
        headers=headers,
        files=files,
        data=data,
    )


@pytest.mark.asyncio
async def test_socket_connect_and_voice_delivery(live_client):
    try:
        health = await live_client.get("/health/live")
    except Exception:
        pytest.skip("Live server is not running on http://api_test:8000")

    assert health.status_code == 200

    sender, sender_tokens = await _create_verified_user_and_tokens("sender@test.com")
    receiver, receiver_tokens = await _create_verified_user_and_tokens(
        "receiver@test.com"
    )

    sender_id = str(sender["_id"])
    receiver_id = str(receiver["_id"])
    await _grant_chat_permission(sender_id, receiver_id)

    sender_sio = socketio.AsyncClient()
    receiver_sio = socketio.AsyncClient()

    receiver_events: list[dict] = []
    sender_status_events: list[dict] = []

    receiver_msg_event = asyncio.Event()
    sender_status_event = asyncio.Event()

    @receiver_sio.on("receive_message")
    async def on_receive_message(data):
        receiver_events.append(data)
        receiver_msg_event.set()

    @sender_sio.on("message_status")
    async def on_message_status(data):
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

        upload_res = await _post_media(
            live_client,
            access_token=sender_tokens["access_token"],
            kind="voice",
            receiver_id=receiver_id,
            filename="sample.mp3",
            content=b"fake-audio",
            mime="audio/mpeg",
            duration_ms=1000,
        )
        assert upload_res.status_code == 201, upload_res.text

        uploaded_message = upload_res.json()["data"]
        message_id = uploaded_message["id"]

        await asyncio.wait_for(receiver_msg_event.wait(), timeout=5)

        assert len(receiver_events) == 1
        message = receiver_events[0].get("message", receiver_events[0])
        assert message["id"] == message_id
        assert message["sender_id"] == sender_id
        assert message["receiver_id"] == receiver_id
        assert message["type"] == "media"
        assert message["media"] is not None
        assert message["media"]["kind"] == "voice"
        assert message["media"]["mime"] == "audio/mpeg"

        await receiver_sio.emit("message_delivered", {"message_id": message_id})

        await asyncio.wait_for(sender_status_event.wait(), timeout=5)

        assert len(sender_status_events) >= 1
        status_payload = sender_status_events[-1]
        assert status_payload["message_id"] == message_id
        assert status_payload["status"] == "delivered"
        assert status_payload["message_type"] == "media"
        assert status_payload["media_kind"] == "voice"

    finally:
        if sender_sio.connected:
            await asyncio.wait_for(sender_sio.disconnect(), timeout=3)
        if receiver_sio.connected:
            await asyncio.wait_for(receiver_sio.disconnect(), timeout=3)
        await asyncio.sleep(0.1)


@pytest.mark.asyncio
async def test_socket_text_message_delivery_and_read(live_client):
    try:
        health = await live_client.get("/health/live")
    except Exception:
        pytest.skip("Live server is not running on http://api_test:8000")

    assert health.status_code == 200

    sender, sender_tokens = await _create_verified_user_and_tokens(
        "sender-text@test.com"
    )
    receiver, receiver_tokens = await _create_verified_user_and_tokens(
        "receiver-text@test.com"
    )

    sender_id = str(sender["_id"])
    receiver_id = str(receiver["_id"])
    await _grant_chat_permission(sender_id, receiver_id)

    sender_sio = await _connect_socket(sender_tokens["access_token"])
    receiver_sio = await _connect_socket(receiver_tokens["access_token"])

    receiver_events: list[dict] = []
    sender_status_events: list[dict] = []
    receiver_msg_event = asyncio.Event()
    sender_status_event = asyncio.Event()

    @receiver_sio.on("receive_message")
    async def on_receive_message(data):
        receiver_events.append(data)
        receiver_msg_event.set()

    @sender_sio.on("message_status")
    async def on_message_status(data):
        sender_status_events.append(data)
        sender_status_event.set()

    try:
        resp = await live_client.post(
            "/messages/text",
            headers={"Authorization": f"Bearer {sender_tokens['access_token']}"},
            json={
                "receiver_id": receiver_id,
                "text": "Բարև 👋 Привет Hello",
            },
        )
        assert resp.status_code == 201, resp.text

        body = resp.json()["data"]
        message_id = body["id"]
        assert body["type"] == "text"
        assert body["text"] == "Բարև 👋 Привет Hello"
        assert body["media"] is None

        await asyncio.wait_for(receiver_msg_event.wait(), timeout=5)

        message = receiver_events[-1].get("message", receiver_events[-1])
        assert message["id"] == message_id
        assert message["sender_id"] == sender_id
        assert message["receiver_id"] == receiver_id
        assert message["type"] == "text"
        assert message["text"] == "Բարև 👋 Привет Hello"

        await receiver_sio.emit("message_read", {"message_id": message_id})
        await asyncio.wait_for(sender_status_event.wait(), timeout=5)

        status_payload = sender_status_events[-1]
        assert status_payload["message_id"] == message_id
        assert status_payload["status"] == "read"
        assert status_payload["message_type"] == "text"

    finally:
        if sender_sio.connected:
            await asyncio.wait_for(sender_sio.disconnect(), timeout=3)
        if receiver_sio.connected:
            await asyncio.wait_for(receiver_sio.disconnect(), timeout=3)
        await asyncio.sleep(0.1)


@pytest.mark.asyncio
async def test_socket_image_message_delivery(live_client):
    try:
        health = await live_client.get("/health/live")
    except Exception:
        pytest.skip("Live server is not running on http://api_test:8000")

    assert health.status_code == 200

    sender, sender_tokens = await _create_verified_user_and_tokens(
        "sender-image@test.com"
    )
    receiver, receiver_tokens = await _create_verified_user_and_tokens(
        "receiver-image@test.com"
    )

    receiver_id = str(receiver["_id"])
    await _grant_chat_permission(str(sender["_id"]), receiver_id)

    sender_sio = await _connect_socket(sender_tokens["access_token"])
    receiver_sio = await _connect_socket(receiver_tokens["access_token"])

    receiver_events: list[dict] = []
    receiver_msg_event = asyncio.Event()

    @receiver_sio.on("receive_message")
    async def on_receive_message(data):
        receiver_events.append(data)
        receiver_msg_event.set()

    try:
        resp = await _post_media(
            live_client,
            access_token=sender_tokens["access_token"],
            kind="image",
            receiver_id=receiver_id,
            filename="photo.png",
            content=b"fake-image-bytes",
            mime="image/png",
            text="caption",
        )
        assert resp.status_code == 201, resp.text

        body = resp.json()["data"]
        assert body["type"] == "media"
        assert body["text"] == "caption"
        assert body["media"] is not None
        assert body["media"]["kind"] == "image"
        assert body["media"]["mime"] == "image/png"

        await asyncio.wait_for(receiver_msg_event.wait(), timeout=5)
        message = receiver_events[-1].get("message", receiver_events[-1])

        assert message["id"] == body["id"]
        assert message["type"] == "media"
        assert message["text"] == "caption"
        assert message["media"] is not None
        assert message["media"]["kind"] == "image"
        assert message["media"]["mime"] == "image/png"

    finally:
        if sender_sio.connected:
            await asyncio.wait_for(sender_sio.disconnect(), timeout=3)
        if receiver_sio.connected:
            await asyncio.wait_for(receiver_sio.disconnect(), timeout=3)
        await asyncio.sleep(0.1)


@pytest.mark.asyncio
async def test_socket_audio_message_delivery(live_client):
    try:
        health = await live_client.get("/health/live")
    except Exception:
        pytest.skip("Live server is not running on http://api_test:8000")

    assert health.status_code == 200

    sender, sender_tokens = await _create_verified_user_and_tokens(
        "sender-audio@test.com"
    )
    receiver, receiver_tokens = await _create_verified_user_and_tokens(
        "receiver-audio@test.com"
    )

    receiver_id = str(receiver["_id"])
    await _grant_chat_permission(str(sender["_id"]), receiver_id)

    sender_sio = await _connect_socket(sender_tokens["access_token"])
    receiver_sio = await _connect_socket(receiver_tokens["access_token"])

    receiver_events: list[dict] = []
    receiver_msg_event = asyncio.Event()

    @receiver_sio.on("receive_message")
    async def on_receive_message(data):
        receiver_events.append(data)
        receiver_msg_event.set()

    try:
        resp = await _post_media(
            live_client,
            access_token=sender_tokens["access_token"],
            kind="audio",
            receiver_id=receiver_id,
            filename="track.mp3",
            content=b"x" * 1024,
            mime="audio/mpeg",
        )
        assert resp.status_code == 201, resp.text

        body = resp.json()["data"]
        assert body["type"] == "media"
        assert body["media"] is not None
        assert body["media"]["kind"] == "audio"
        assert body["media"]["mime"] == "audio/mpeg"

        await asyncio.wait_for(receiver_msg_event.wait(), timeout=5)
        message = receiver_events[-1].get("message", receiver_events[-1])

        assert message["id"] == body["id"]
        assert message["type"] == "media"
        assert message["media"] is not None
        assert message["media"]["kind"] == "audio"
        assert message["media"]["mime"] == "audio/mpeg"

    finally:
        if sender_sio.connected:
            await asyncio.wait_for(sender_sio.disconnect(), timeout=3)
        if receiver_sio.connected:
            await asyncio.wait_for(receiver_sio.disconnect(), timeout=3)
        await asyncio.sleep(0.1)


@pytest.mark.asyncio
async def test_socket_thread_and_reaction_events_are_emitted_to_both_users(live_client):
    try:
        health = await live_client.get("/health/live")
    except Exception:
        pytest.skip("Live server is not running on http://api_test:8000")

    assert health.status_code == 200

    sender, sender_tokens = await _create_verified_user_and_tokens(
        "sender-thread-events@test.com"
    )
    receiver, receiver_tokens = await _create_verified_user_and_tokens(
        "receiver-thread-events@test.com"
    )

    sender_id = str(sender["_id"])
    receiver_id = str(receiver["_id"])
    await _grant_chat_permission(sender_id, receiver_id)

    sender_sio = await _connect_socket(sender_tokens["access_token"])
    receiver_sio = await _connect_socket(receiver_tokens["access_token"])

    sender_thread_events: list[dict] = []
    receiver_thread_events: list[dict] = []
    sender_summary_events: list[dict] = []
    receiver_summary_events: list[dict] = []
    sender_reaction_events: list[dict] = []
    receiver_reaction_events: list[dict] = []

    sender_thread_event = asyncio.Event()
    receiver_thread_event = asyncio.Event()
    sender_summary_event = asyncio.Event()
    receiver_summary_event = asyncio.Event()
    sender_reaction_event = asyncio.Event()
    receiver_reaction_event = asyncio.Event()

    @sender_sio.on("thread_reply_created")
    async def on_sender_thread_reply_created(data):
        sender_thread_events.append(data)
        sender_thread_event.set()

    @receiver_sio.on("thread_reply_created")
    async def on_receiver_thread_reply_created(data):
        receiver_thread_events.append(data)
        receiver_thread_event.set()

    @sender_sio.on("thread_summary_updated")
    async def on_sender_thread_summary_updated(data):
        sender_summary_events.append(data)
        sender_summary_event.set()

    @receiver_sio.on("thread_summary_updated")
    async def on_receiver_thread_summary_updated(data):
        receiver_summary_events.append(data)
        receiver_summary_event.set()

    @sender_sio.on("message_reacted")
    async def on_sender_message_reacted(data):
        sender_reaction_events.append(data)
        sender_reaction_event.set()

    @receiver_sio.on("message_reacted")
    async def on_receiver_message_reacted(data):
        receiver_reaction_events.append(data)
        receiver_reaction_event.set()

    try:
        root_resp = await live_client.post(
            "/messages/text",
            headers={"Authorization": f"Bearer {sender_tokens['access_token']}"},
            json={
                "receiver_id": receiver_id,
                "text": "root for realtime thread",
            },
        )
        assert root_resp.status_code == 201, root_resp.text
        root_message = root_resp.json()["data"]

        thread_resp = await live_client.post(
            "/messages/text",
            headers={"Authorization": f"Bearer {receiver_tokens['access_token']}"},
            json={
                "receiver_id": sender_id,
                "text": "thread event payload",
                "reply_mode": "thread",
                "reply_to_message_id": root_message["id"],
            },
        )
        assert thread_resp.status_code == 201, thread_resp.text
        thread_message = thread_resp.json()["data"]

        await asyncio.wait_for(sender_thread_event.wait(), timeout=5)
        await asyncio.wait_for(receiver_thread_event.wait(), timeout=5)
        await asyncio.wait_for(sender_summary_event.wait(), timeout=5)
        await asyncio.wait_for(receiver_summary_event.wait(), timeout=5)

        assert sender_thread_events[-1]["id"] == thread_message["id"]
        assert receiver_thread_events[-1]["id"] == thread_message["id"]
        assert sender_summary_events[-1]["thread_root_id"] == root_message["id"]
        assert receiver_summary_events[-1]["thread_root_id"] == root_message["id"]
        assert sender_summary_events[-1]["thread_reply_count"] == 1
        assert receiver_summary_events[-1]["thread_reply_count"] == 1

        react_resp = await live_client.post(
            f"/messages/{root_message['id']}/reactions",
            headers={"Authorization": f"Bearer {sender_tokens['access_token']}"},
            json={"emoji": "🔥"},
        )
        assert react_resp.status_code == 200, react_resp.text

        await asyncio.wait_for(sender_reaction_event.wait(), timeout=5)
        await asyncio.wait_for(receiver_reaction_event.wait(), timeout=5)

        assert sender_reaction_events[-1]["message_id"] == root_message["id"]
        assert receiver_reaction_events[-1]["message_id"] == root_message["id"]
        assert sender_reaction_events[-1]["reactions"][0]["count"] == 1
        assert receiver_reaction_events[-1]["reactions"][0]["emoji"] == "🔥"

    finally:
        if sender_sio.connected:
            await asyncio.wait_for(sender_sio.disconnect(), timeout=3)
        if receiver_sio.connected:
            await asyncio.wait_for(receiver_sio.disconnect(), timeout=3)
        await asyncio.sleep(0.1)


@pytest.mark.asyncio
async def test_socket_video_message_delivery(live_client):
    try:
        health = await live_client.get("/health/live")
    except Exception:
        pytest.skip("Live server is not running on http://api_test:8000")

    assert health.status_code == 200

    sender, sender_tokens = await _create_verified_user_and_tokens(
        "sender-video@test.com"
    )
    receiver, receiver_tokens = await _create_verified_user_and_tokens(
        "receiver-video@test.com"
    )

    receiver_id = str(receiver["_id"])
    await _grant_chat_permission(str(sender["_id"]), receiver_id)

    sender_sio = await _connect_socket(sender_tokens["access_token"])
    receiver_sio = await _connect_socket(receiver_tokens["access_token"])

    receiver_events: list[dict] = []
    receiver_msg_event = asyncio.Event()

    @receiver_sio.on("receive_message")
    async def on_receive_message(data):
        receiver_events.append(data)
        receiver_msg_event.set()

    try:
        resp = await _post_media(
            live_client,
            access_token=sender_tokens["access_token"],
            kind="video",
            receiver_id=receiver_id,
            filename="clip.mp4",
            content=b"fake-video-bytes",
            mime="video/mp4",
            text="video caption",
        )
        assert resp.status_code == 201, resp.text

        body = resp.json()["data"]
        assert body["type"] == "media"
        assert body["text"] == "video caption"
        assert body["media"] is not None
        assert body["media"]["kind"] == "video"
        assert body["media"]["mime"] == "video/mp4"

        await asyncio.wait_for(receiver_msg_event.wait(), timeout=5)
        message = receiver_events[-1].get("message", receiver_events[-1])

        assert message["id"] == body["id"]
        assert message["type"] == "media"
        assert message["text"] == "video caption"
        assert message["media"] is not None
        assert message["media"]["kind"] == "video"
        assert message["media"]["mime"] == "video/mp4"

    finally:
        if sender_sio.connected:
            await asyncio.wait_for(sender_sio.disconnect(), timeout=3)
        if receiver_sio.connected:
            await asyncio.wait_for(receiver_sio.disconnect(), timeout=3)
        await asyncio.sleep(0.1)


@pytest.mark.asyncio
async def test_socket_typing_and_send_message_require_chat_permission(live_client):
    try:
        health = await live_client.get("/health/live")
    except Exception:
        pytest.skip("Live server is not running on http://api_test:8000")

    assert health.status_code == 200

    sender, sender_tokens = await _create_verified_user_and_tokens(
        "sender-no-chat@test.com"
    )
    receiver, _ = await _create_verified_user_and_tokens("receiver-no-chat@test.com")

    sender_sio = await _connect_socket(sender_tokens["access_token"])
    receiver_id = str(receiver["_id"])
    error_event = asyncio.Event()
    errors: list[dict] = []

    @sender_sio.on("error")
    async def on_error(data):
        errors.append(data)
        error_event.set()

    try:
        await sender_sio.emit("typing_start", {"to": receiver_id})
        await asyncio.wait_for(error_event.wait(), timeout=5)
        assert errors[-1]["code"] == "CHAT_PERMISSION_REQUIRED"

        error_event.clear()

        await sender_sio.emit(
            "send_message",
            {"to": receiver_id, "message_id": str(ObjectId()), "type": "text"},
        )
        await asyncio.wait_for(error_event.wait(), timeout=5)
        assert errors[-1]["code"] == "CHAT_PERMISSION_REQUIRED"
    finally:
        if sender_sio.connected:
            await asyncio.wait_for(sender_sio.disconnect(), timeout=3)
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
