import uuid
from io import BytesIO

import pytest

from app.db.mongo import get_db
from app.modules.auth import service as auth_service
from app.modules.messages import router as messages_router_module


async def _verified_user_tokens(client, email: str, monkeypatch):
    fixed_code = "123456"
    monkeypatch.setattr(auth_service, "_generate_6_digit_code", lambda: fixed_code)

    register_res = await client.post("/auth/register", json={"email": email})
    assert register_res.status_code == 200

    verify_res = await client.post(
        "/auth/verify",
        json={"email": email, "code": fixed_code},
    )
    assert verify_res.status_code == 200

    return verify_res.json()["data"]


@pytest.mark.skip(reason="Run single")
async def test_upload_voice_and_history(inprocess_client, monkeypatch):
    async def _noop_emit(*args, **kwargs):
        return None

    monkeypatch.setattr(messages_router_module, "emit_voice_message_to_receiver", _noop_emit)

    sender_email = f"sender-{uuid.uuid4().hex[:8]}@test.com"
    receiver_email = f"receiver-{uuid.uuid4().hex[:8]}@test.com"

    sender_tokens = await _verified_user_tokens(inprocess_client, sender_email, monkeypatch)
    receiver_tokens = await _verified_user_tokens(inprocess_client, receiver_email, monkeypatch)

    db = get_db()
    sender = await db["users"].find_one({"email": sender_email.lower()})
    receiver = await db["users"].find_one({"email": receiver_email.lower()})

    headers = {"Authorization": f"Bearer {sender_tokens['access_token']}"}
    files = {
        "file": ("sample.mp3", BytesIO(b"fake-audio"), "audio/mpeg"),
    }
    data = {
        "receiver_id": str(receiver["_id"]),
        "duration_ms": "1000",
    }

    upload = await inprocess_client.post("/messages/voice", headers=headers, files=files, data=data, timeout=10)
    assert upload.status_code == 201

    history = await inprocess_client.get(
        f"/messages/{sender['_id']}",
        headers={"Authorization": f"Bearer {receiver_tokens['access_token']}"},
        timeout=10,
    )
    assert history.status_code == 200

    payload = history.json()["data"]
    assert len(payload) == 1