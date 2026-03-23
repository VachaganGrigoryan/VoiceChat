import pytest

from app.tests.integration.test_realtime_socket import (
    _create_verified_user_and_tokens,
    _grant_chat_permission,
    _insert_active_sticker_pack,
    _post_media,
)


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
async def test_send_sticker_message_returns_reference_payload(inprocess_client):
    sender, sender_tokens = await _create_verified_user_and_tokens("sender-sticker-message@test.com")
    receiver, _ = await _create_verified_user_and_tokens("receiver-sticker-message@test.com")
    await _grant_chat_permission(str(sender["_id"]), str(receiver["_id"]))
    _, sticker = await _insert_active_sticker_pack(owner_user_id=str(sender["_id"]))

    resp = await inprocess_client.post(
        "/messages/sticker",
        headers=_auth_headers(sender_tokens["access_token"]),
        json={
            "receiver_id": str(receiver["_id"]),
            "sticker_id": str(sticker["_id"]),
            "emoji": "🎉",
        },
    )

    assert resp.status_code == 201, resp.text
    body = resp.json()["data"]
    assert body["type"] == "sticker"
    assert body["media"] is not None
    assert body["media"]["storage"] == "local"
    assert body["media"]["mime"] == "image/webp"
    assert body["media"]["key"] == sticker["storage_key"]
    assert body["sticker"]["sticker_id"] == str(sticker["_id"])
    assert body["sticker"]["emoji"] == "🎉"

    history = await inprocess_client.get(
        f"/messages/conversations/{receiver['_id']}",
        headers=_auth_headers(sender_tokens["access_token"]),
    )
    assert history.status_code == 200, history.text
    item = history.json()["data"][0]
    assert item["type"] == "sticker"
    assert item["media"] is not None
    assert item["media"]["storage"] == "local"
    assert item["media"]["key"] == sticker["storage_key"]
    assert item["sticker"]["sticker_id"] == str(sticker["_id"])

    conversations = await inprocess_client.get(
        "/messages/conversations",
        headers=_auth_headers(sender_tokens["access_token"]),
    )
    assert conversations.status_code == 200, conversations.text
    last_message = conversations.json()["data"][0]["last_message"]
    assert last_message["type"] == "sticker"
    assert last_message["media"] is not None
    assert last_message["media"]["storage"] == "local"
    assert last_message["media"]["key"] == sticker["storage_key"]


@pytest.mark.asyncio
async def test_history_keeps_sticker_ref_when_asset_is_missing(inprocess_client):
    from datetime import UTC, datetime

    from bson import ObjectId

    from app.db.mongo import get_db
    from app.modules.messages.repository import conversation_id_for

    sender, sender_tokens = await _create_verified_user_and_tokens("sender-missing-sticker@test.com")
    receiver, _ = await _create_verified_user_and_tokens("receiver-missing-sticker@test.com")
    sender_id = str(sender["_id"])
    receiver_id = str(receiver["_id"])
    await _grant_chat_permission(sender_id, receiver_id)

    now = datetime.now(UTC)
    db = get_db()
    missing_sticker_id = str(ObjectId())
    await db["messages"].insert_one(
        {
            "conversation_id": conversation_id_for(sender_id, receiver_id),
            "sender_id": ObjectId(sender_id),
            "receiver_id": ObjectId(receiver_id),
            "type": "sticker",
            "text": None,
            "media": None,
            "sticker": {
                "sticker_id": missing_sticker_id,
                "pack_id": str(ObjectId()),
                "pack_slug": "missing_pack",
                "sticker_slug": "missing_sticker",
                "emoji": "🎉",
                "version": 1,
            },
            "hidden_for_user_ids": [],
            "status": "sent",
            "edited_at": None,
            "delivered_at": None,
            "read_at": None,
            "reply_mode": None,
            "reply_to_message_id": None,
            "thread_root_id": None,
            "reply_preview": None,
            "is_thread_root": False,
            "thread_reply_count": 0,
            "last_thread_reply_at": None,
            "reactions": [],
            "created_at": now,
            "updated_at": now,
        }
    )

    history = await inprocess_client.get(
        f"/messages/conversations/{receiver_id}",
        headers=_auth_headers(sender_tokens["access_token"]),
    )
    assert history.status_code == 200, history.text
    item = history.json()["data"][0]
    assert item["type"] == "sticker"
    assert item["sticker"]["sticker_id"] == missing_sticker_id
    assert item["media"] is None


@pytest.mark.asyncio
async def test_send_sticker_message_rejects_non_owner(inprocess_client):
    owner, owner_tokens = await _create_verified_user_and_tokens("owner-sticker@test.com")
    sender, sender_tokens = await _create_verified_user_and_tokens("sender-non-owner-sticker@test.com")
    receiver, _ = await _create_verified_user_and_tokens("receiver-owner-sticker@test.com")
    await _grant_chat_permission(str(sender["_id"]), str(receiver["_id"]))
    _, sticker = await _insert_active_sticker_pack(owner_user_id=str(owner["_id"]))

    resp = await inprocess_client.post(
        "/messages/sticker",
        headers=_auth_headers(sender_tokens["access_token"]),
        json={
            "receiver_id": str(receiver["_id"]),
            "sticker_id": str(sticker["_id"]),
            "emoji": "🎉",
        },
    )
    assert resp.status_code == 404, resp.text
    assert resp.json()["error"]["code"] == "STICKER_NOT_FOUND"


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
    media_url = owner_message["media"]["url"]
    media_get_resp = await live_client.get(media_url)
    assert media_get_resp.status_code == 200, media_get_resp.text
    assert media_get_resp.content == b"owned-audio"

    owner_delete_resp = await live_client.delete(
        f"/messages/{owner_message['id']}",
        headers=_auth_headers(sender_tokens["access_token"]),
    )
    assert owner_delete_resp.status_code == 200, owner_delete_resp.text
    owner_delete_body = owner_delete_resp.json()["data"]
    assert owner_delete_body["deleted_for_everyone"] is True
    assert owner_delete_body["hidden_for_me"] is False
    assert owner_delete_body["deleted_media"] is True
    media_after_delete_resp = await live_client.get(media_url)
    assert media_after_delete_resp.status_code == 404, media_after_delete_resp.text

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


@pytest.mark.asyncio
async def test_quote_replies_stay_in_history_and_thread_replies_use_thread_endpoints(inprocess_client):
    sender, sender_tokens = await _create_verified_user_and_tokens("sender-threads@test.com")
    receiver, receiver_tokens = await _create_verified_user_and_tokens("receiver-threads@test.com")
    sender_id = str(sender["_id"])
    receiver_id = str(receiver["_id"])
    await _grant_chat_permission(sender_id, receiver_id)

    root_resp = await inprocess_client.post(
        "/messages/text",
        headers=_auth_headers(sender_tokens["access_token"]),
        json={
            "receiver_id": receiver_id,
            "text": "root message",
        },
    )
    assert root_resp.status_code == 201, root_resp.text
    root_message = root_resp.json()["data"]

    quote_resp = await inprocess_client.post(
        "/messages/text",
        headers=_auth_headers(sender_tokens["access_token"]),
        json={
            "receiver_id": receiver_id,
            "text": "quoted in main chat",
            "reply_mode": "quote",
            "reply_to_message_id": root_message["id"],
        },
    )
    assert quote_resp.status_code == 201, quote_resp.text
    quote_message = quote_resp.json()["data"]
    assert quote_message["reply_mode"] == "quote"
    assert quote_message["thread_root_id"] is None
    assert quote_message["reply_preview"]["message_id"] == root_message["id"]
    assert quote_message["reply_preview"]["text"] == "root message"

    thread_resp = await inprocess_client.post(
        "/messages/text",
        headers=_auth_headers(receiver_tokens["access_token"]),
        json={
            "receiver_id": sender_id,
            "text": "reply in thread",
            "reply_mode": "thread",
            "reply_to_message_id": root_message["id"],
        },
    )
    assert thread_resp.status_code == 201, thread_resp.text
    thread_message = thread_resp.json()["data"]
    assert thread_message["reply_mode"] == "thread"
    assert thread_message["thread_root_id"] == root_message["id"]
    assert thread_message["reply_preview"]["message_id"] == root_message["id"]

    history_resp = await inprocess_client.get(
        f"/messages/conversations/{receiver_id}",
        headers=_auth_headers(sender_tokens["access_token"]),
    )
    assert history_resp.status_code == 200, history_resp.text
    history_items = history_resp.json()["data"]
    history_ids = [item["id"] for item in history_items]
    assert root_message["id"] in history_ids
    assert quote_message["id"] in history_ids
    assert thread_message["id"] not in history_ids

    thread_list_resp = await inprocess_client.get(
        f"/messages/{root_message['id']}/thread",
        headers=_auth_headers(sender_tokens["access_token"]),
    )
    assert thread_list_resp.status_code == 200, thread_list_resp.text
    thread_items = thread_list_resp.json()["data"]
    assert len(thread_items) == 1
    assert thread_items[0]["id"] == thread_message["id"]

    summary_resp = await inprocess_client.get(
        f"/messages/{root_message['id']}/thread-summary",
        headers=_auth_headers(sender_tokens["access_token"]),
    )
    assert summary_resp.status_code == 200, summary_resp.text
    summary = summary_resp.json()["data"]
    assert summary["thread_root_id"] == root_message["id"]
    assert summary["thread_reply_count"] == 1
    assert summary["is_thread_root"] is True
    assert summary["last_thread_reply_at"] is not None


@pytest.mark.asyncio
async def test_reactions_are_grouped_and_returned_in_history(inprocess_client):
    sender, sender_tokens = await _create_verified_user_and_tokens("sender-reactions@test.com")
    receiver, receiver_tokens = await _create_verified_user_and_tokens("receiver-reactions@test.com")
    sender_id = str(sender["_id"])
    receiver_id = str(receiver["_id"])
    await _grant_chat_permission(sender_id, receiver_id)

    create_resp = await inprocess_client.post(
        "/messages/text",
        headers=_auth_headers(sender_tokens["access_token"]),
        json={
            "receiver_id": receiver_id,
            "text": "react here",
        },
    )
    assert create_resp.status_code == 201, create_resp.text
    message = create_resp.json()["data"]

    sender_fire = await inprocess_client.post(
        f"/messages/{message['id']}/reactions",
        headers=_auth_headers(sender_tokens["access_token"]),
        json={"emoji": "🔥"},
    )
    assert sender_fire.status_code == 200, sender_fire.text
    assert sender_fire.json()["data"]["reactions"][0]["count"] == 1

    receiver_fire = await inprocess_client.post(
        f"/messages/{message['id']}/reactions",
        headers=_auth_headers(receiver_tokens["access_token"]),
        json={"emoji": "🔥"},
    )
    assert receiver_fire.status_code == 200, receiver_fire.text
    fire_group = receiver_fire.json()["data"]["reactions"][0]
    assert fire_group["emoji"] == "🔥"
    assert fire_group["count"] == 2
    assert set(fire_group["user_ids"]) == {sender_id, receiver_id}

    sender_heart = await inprocess_client.post(
        f"/messages/{message['id']}/reactions",
        headers=_auth_headers(sender_tokens["access_token"]),
        json={"emoji": "❤️"},
    )
    assert sender_heart.status_code == 200, sender_heart.text
    assert len(sender_heart.json()["data"]["reactions"]) == 2

    remove_sender_fire = await inprocess_client.delete(
        f"/messages/{message['id']}/reactions/%F0%9F%94%A5/me",
        headers=_auth_headers(sender_tokens["access_token"]),
    )
    assert remove_sender_fire.status_code == 200, remove_sender_fire.text
    reactions = remove_sender_fire.json()["data"]["reactions"]
    assert len(reactions) == 2

    remaining_fire = next(reaction for reaction in reactions if reaction["emoji"] == "🔥")
    assert remaining_fire["count"] == 1
    assert remaining_fire["user_ids"] == [receiver_id]

    history_resp = await inprocess_client.get(
        f"/messages/conversations/{receiver_id}",
        headers=_auth_headers(sender_tokens["access_token"]),
    )
    assert history_resp.status_code == 200, history_resp.text
    history_message = history_resp.json()["data"][0]
    assert len(history_message["reactions"]) == 2
    assert next(reaction for reaction in history_message["reactions"] if reaction["emoji"] == "🔥")["count"] == 1
