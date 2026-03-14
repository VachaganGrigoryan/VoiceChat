import pytest

from app.tests.integration.test_realtime_socket import _create_verified_user_and_tokens, _post_media


@pytest.mark.asyncio
async def test_media_upload_rejects_unsupported_type(live_client):
    try:
        health = await live_client.get("/health/live")
    except Exception:
        pytest.skip("Live server is not running on http://api_test:8000")

    assert health.status_code == 200

    sender, sender_tokens = await _create_verified_user_and_tokens("sender-bad-type@test.com")
    receiver, _ = await _create_verified_user_and_tokens("receiver-bad-type@test.com")

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

