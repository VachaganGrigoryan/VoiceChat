import os

import pytest

from app.core.config import settings
from app.tests.integration.test_realtime_socket import _create_verified_user_and_tokens, _grant_chat_permission, _post_media


def _auth_headers(access_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {access_token}"}


@pytest.mark.asyncio
async def test_media_upload_rejects_unsupported_type(live_client):
    try:
        health = await live_client.get("/health/live")
    except Exception:
        pytest.skip("Live server is not running on http://api_test:8000")

    assert health.status_code == 200

    sender, sender_tokens = await _create_verified_user_and_tokens("sender-bad-type@test.com")
    receiver, _ = await _create_verified_user_and_tokens("receiver-bad-type@test.com")
    await _grant_chat_permission(str(sender["_id"]), str(receiver["_id"]))

    resp = await _post_media(
        live_client,
        access_token=sender_tokens["access_token"],
        kind="image",
        receiver_id=str(receiver["_id"]),
        filename="bad.txt",
        content=b"hello",
        mime="text/plain",
    )

    assert resp.status_code == 415, resp.text
    body = resp.json()
    assert body["error"]["code"] == "UNSUPPORTED_MEDIA_TYPE"


@pytest.mark.asyncio
async def test_sticker_upload_rejects_too_large(live_client):
    try:
        health = await live_client.get("/health/live")
    except Exception:
        pytest.skip("Live server is not running on http://api_test:8000")

    assert health.status_code == 200

    sender, sender_tokens = await _create_verified_user_and_tokens("sender-big-sticker@test.com")
    receiver, _ = await _create_verified_user_and_tokens("receiver-big-sticker@test.com")
    await _grant_chat_permission(str(sender["_id"]), str(receiver["_id"]))

    too_big = b"x" * (2 * 1024 * 1024 + 1)

    resp = await _post_media(
        live_client,
        access_token=sender_tokens["access_token"],
        kind="sticker",
        receiver_id=str(receiver["_id"]),
        filename="sticker.webp",
        content=too_big,
        mime="image/webp",
    )

    assert resp.status_code == 413, resp.text
    body = resp.json()
    assert body["error"]["code"] == "FILE_TOO_LARGE"


@pytest.mark.asyncio
async def test_send_text_rejects_empty(live_client):
    try:
        health = await live_client.get("/health/live")
    except Exception:
        pytest.skip("Live server is not running on http://api_test:8000")

    assert health.status_code == 200

    sender, sender_tokens = await _create_verified_user_and_tokens("sender-empty-text@test.com")
    receiver, _ = await _create_verified_user_and_tokens("receiver-empty-text@test.com")
    await _grant_chat_permission(str(sender["_id"]), str(receiver["_id"]))

    resp = await live_client.post(
        "/messages/text",
        headers={"Authorization": f"Bearer {sender_tokens['access_token']}"},
        json={
            "receiver_id": str(receiver["_id"]),
            "text": "   ",
        },
    )

    assert resp.status_code == 400, resp.text
    body = resp.json()
    assert body["error"]["code"] == "TEXT_REQUIRED"


@pytest.mark.asyncio
async def test_conversations_endpoint_returns_recent_items(live_client):
    try:
        health = await live_client.get("/health/live")
    except Exception:
        pytest.skip("Live server is not running on http://api_test:8000")

    assert health.status_code == 200

    sender, sender_tokens = await _create_verified_user_and_tokens("sender-conversations@test.com")
    receiver, _ = await _create_verified_user_and_tokens("receiver-conversations@test.com")

    receiver_id = str(receiver["_id"])
    await _grant_chat_permission(str(sender["_id"]), receiver_id)

    text_resp = await live_client.post(
        "/messages/text",
        headers={"Authorization": f"Bearer {sender_tokens['access_token']}"},
        json={
            "receiver_id": receiver_id,
            "text": "conversation preview",
        },
    )
    assert text_resp.status_code == 201, text_resp.text

    conv_resp = await live_client.get(
        "/messages/conversations?limit=10",
        headers={"Authorization": f"Bearer {sender_tokens['access_token']}"},
    )
    assert conv_resp.status_code == 200, conv_resp.text

    body = conv_resp.json()
    assert body["success"] is True
    assert isinstance(body["data"], list)
    assert body["meta"]["limit"] == 10
    assert len(body["data"]) >= 1

    item = body["data"][0]
    assert "conversation_id" in item
    assert "peer_user" in item
    assert "last_message" in item
    assert "last_message_at" in item

    assert item["peer_user"]["id"] == receiver_id
    assert item["last_message"]["type"] == "text"
    assert item["last_message"]["text"] == "conversation preview"


@pytest.mark.asyncio
async def test_message_history_new_route_and_cursor(live_client):
    try:
        health = await live_client.get("/health/live")
    except Exception:
        pytest.skip("Live server is not running on http://api_test:8000")

    assert health.status_code == 200

    sender, sender_tokens = await _create_verified_user_and_tokens("sender-history@test.com")
    receiver, _ = await _create_verified_user_and_tokens("receiver-history@test.com")

    receiver_id = str(receiver["_id"])
    await _grant_chat_permission(str(sender["_id"]), receiver_id)

    for i in range(3):
        resp = await live_client.post(
            "/messages/text",
            headers={"Authorization": f"Bearer {sender_tokens['access_token']}"},
            json={
                "receiver_id": receiver_id,
                "text": f"history {i}",
            },
        )
        assert resp.status_code == 201, resp.text

    first = await live_client.get(
        f"/messages/conversations/{receiver_id}?limit=2",
        headers={"Authorization": f"Bearer {sender_tokens['access_token']}"},
    )
    assert first.status_code == 200, first.text

    first_body = first.json()
    assert first_body["success"] is True
    assert len(first_body["data"]) == 2
    assert first_body["meta"]["limit"] == 2

    next_cursor = first_body["meta"]["next_cursor"]
    assert next_cursor is not None

    second = await live_client.get(
        f"/messages/conversations/{receiver_id}?limit=2&cursor={next_cursor}",
        headers={"Authorization": f"Bearer {sender_tokens['access_token']}"},
    )
    assert second.status_code == 200, second.text

    second_body = second.json()
    assert second_body["success"] is True
    assert len(second_body["data"]) >= 1


@pytest.mark.asyncio
async def test_conversations_endpoint_cursor(live_client):
    try:
        health = await live_client.get("/health/live")
    except Exception:
        pytest.skip("Live server is not running on http://api_test:8000")

    assert health.status_code == 200

    sender, sender_tokens = await _create_verified_user_and_tokens("sender-conv-cursor@test.com")
    receiver_a, _ = await _create_verified_user_and_tokens("receiver-a@test.com")
    receiver_b, _ = await _create_verified_user_and_tokens("receiver-b@test.com")

    await _grant_chat_permission(str(sender["_id"]), str(receiver_a["_id"]))
    await _grant_chat_permission(str(sender["_id"]), str(receiver_b["_id"]))

    for receiver in (receiver_a, receiver_b):
        resp = await live_client.post(
            "/messages/text",
            headers={"Authorization": f"Bearer {sender_tokens['access_token']}"},
            json={
                "receiver_id": str(receiver["_id"]),
                "text": f"hello {receiver['email']}",
            },
        )
        assert resp.status_code == 201, resp.text

    first = await live_client.get(
        "/messages/conversations?limit=1",
        headers={"Authorization": f"Bearer {sender_tokens['access_token']}"},
    )
    assert first.status_code == 200, first.text

    first_body = first.json()
    assert len(first_body["data"]) == 1
    assert first_body["meta"]["next_cursor"] is not None

    second = await live_client.get(
        f"/messages/conversations?limit=1&cursor={first_body['meta']['next_cursor']}",
        headers={"Authorization": f"Bearer {sender_tokens['access_token']}"},
    )
    assert second.status_code == 200, second.text
    second_body = second.json()
    assert len(second_body["data"]) == 1


@pytest.mark.asyncio
async def test_media_upload_validates_caption_and_duration(inprocess_client):
    sender, sender_tokens = await _create_verified_user_and_tokens("sender-media-validate@test.com")
    receiver, _ = await _create_verified_user_and_tokens("receiver-media-validate@test.com")
    await _grant_chat_permission(str(sender["_id"]), str(receiver["_id"]))

    caption_resp = await _post_media(
        inprocess_client,
        access_token=sender_tokens["access_token"],
        kind="image",
        receiver_id=str(receiver["_id"]),
        filename="photo.png",
        content=b"fake-image",
        mime="image/png",
        text="x" * 4001,
    )
    assert caption_resp.status_code == 400, caption_resp.text
    assert caption_resp.json()["error"]["code"] == "TEXT_TOO_LONG"

    duration_resp = await _post_media(
        inprocess_client,
        access_token=sender_tokens["access_token"],
        kind="voice",
        receiver_id=str(receiver["_id"]),
        filename="sample.mp3",
        content=b"fake-audio",
        mime="audio/mpeg",
        duration_ms=-1,
    )
    assert duration_resp.status_code == 400, duration_resp.text
    assert duration_resp.json()["error"]["code"] == "INVALID_DURATION_MS"


@pytest.mark.asyncio
async def test_delete_message_hard_deletes_owned_media_and_hides_for_peer(live_client):
    try:
        health = await live_client.get("/health/live")
    except Exception:
        pytest.skip("Live server is not running on http://api_test:8000")

    assert health.status_code == 200

    sender, sender_tokens = await _create_verified_user_and_tokens("sender-delete@test.com")
    receiver, receiver_tokens = await _create_verified_user_and_tokens("receiver-delete@test.com")
    sender_id = str(sender["_id"])
    receiver_id = str(receiver["_id"])
    await _grant_chat_permission(sender_id, receiver_id)

    owner_message_resp = await _post_media(
        live_client,
        access_token=sender_tokens["access_token"],
        kind="voice",
        receiver_id=receiver_id,
        filename="owned.mp3",
        content=b"owned-audio",
        mime="audio/mpeg",
        duration_ms=1200,
    )
    assert owner_message_resp.status_code == 201, owner_message_resp.text
    owner_message = owner_message_resp.json()["data"]
    media_key = owner_message["media"]["key"]
    media_path = os.path.join(settings.upload_dir, media_key)
    assert os.path.exists(media_path)

    owner_delete_resp = await live_client.delete(
        f"/messages/{owner_message['id']}",
        headers=_auth_headers(sender_tokens["access_token"]),
    )
    assert owner_delete_resp.status_code == 200, owner_delete_resp.text
    owner_delete_body = owner_delete_resp.json()["data"]
    assert owner_delete_body["deleted_for_everyone"] is True
    assert owner_delete_body["hidden_for_me"] is False
    assert owner_delete_body["deleted_media"] is True
    assert os.path.exists(media_path) is False

    sender_history_after_owner_delete = await live_client.get(
        f"/messages/conversations/{receiver_id}",
        headers=_auth_headers(sender_tokens["access_token"]),
    )
    assert sender_history_after_owner_delete.status_code == 200, sender_history_after_owner_delete.text
    assert sender_history_after_owner_delete.json()["data"] == []

    peer_message_resp = await live_client.post(
        "/messages/text",
        headers=_auth_headers(sender_tokens["access_token"]),
        json={
            "receiver_id": receiver_id,
            "text": "visible only to sender after peer hide",
        },
    )
    assert peer_message_resp.status_code == 201, peer_message_resp.text
    peer_message = peer_message_resp.json()["data"]

    peer_delete_resp = await live_client.delete(
        f"/messages/{peer_message['id']}",
        headers=_auth_headers(receiver_tokens["access_token"]),
    )
    assert peer_delete_resp.status_code == 200, peer_delete_resp.text
    peer_delete_body = peer_delete_resp.json()["data"]
    assert peer_delete_body["deleted_for_everyone"] is False
    assert peer_delete_body["hidden_for_me"] is True
    assert peer_delete_body["deleted_media"] is False

    receiver_history = await live_client.get(
        f"/messages/conversations/{sender_id}",
        headers=_auth_headers(receiver_tokens["access_token"]),
    )
    assert receiver_history.status_code == 200, receiver_history.text
    assert receiver_history.json()["data"] == []

    sender_history = await live_client.get(
        f"/messages/conversations/{receiver_id}",
        headers=_auth_headers(sender_tokens["access_token"]),
    )
    assert sender_history.status_code == 200, sender_history.text
    sender_history_items = sender_history.json()["data"]
    assert len(sender_history_items) == 1
    assert sender_history_items[0]["id"] == peer_message["id"]


@pytest.mark.asyncio
async def test_edit_deleted_message_returns_not_found(live_client):
    try:
        health = await live_client.get("/health/live")
    except Exception:
        pytest.skip("Live server is not running on http://api_test:8000")

    assert health.status_code == 200

    sender, sender_tokens = await _create_verified_user_and_tokens("sender-edit-deleted@test.com")
    receiver, _ = await _create_verified_user_and_tokens("receiver-edit-deleted@test.com")
    sender_id = str(sender["_id"])
    receiver_id = str(receiver["_id"])
    await _grant_chat_permission(sender_id, receiver_id)

    create_resp = await live_client.post(
        "/messages/text",
        headers=_auth_headers(sender_tokens["access_token"]),
        json={
            "receiver_id": receiver_id,
            "text": "delete then edit",
        },
    )
    assert create_resp.status_code == 201, create_resp.text
    message_id = create_resp.json()["data"]["id"]

    delete_resp = await live_client.delete(
        f"/messages/{message_id}",
        headers=_auth_headers(sender_tokens["access_token"]),
    )
    assert delete_resp.status_code == 200, delete_resp.text

    edit_resp = await live_client.patch(
        f"/messages/{message_id}",
        headers=_auth_headers(sender_tokens["access_token"]),
        json={"text": "edited"},
    )
    assert edit_resp.status_code == 404, edit_resp.text
    assert edit_resp.json()["error"]["code"] == "MESSAGE_NOT_FOUND"
