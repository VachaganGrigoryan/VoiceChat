from __future__ import annotations

import uuid

import pytest

from app.tests.integration.test_realtime_socket import (
    _create_verified_user_and_tokens,
    _grant_chat_permission,
)


@pytest.mark.asyncio
async def test_contacts_include_existing_dm_conversation(inprocess_client):
    user, user_tokens = await _create_verified_user_and_tokens(
        f"contacts-a-{uuid.uuid4().hex[:8]}@test.com"
    )
    peer, _ = await _create_verified_user_and_tokens(
        f"contacts-b-{uuid.uuid4().hex[:8]}@test.com"
    )
    await _grant_chat_permission(str(user["_id"]), str(peer["_id"]))

    conversation_res = await inprocess_client.post(
        "/conversations",
        headers={"Authorization": f"Bearer {user_tokens['access_token']}"},
        json={"peer_user_id": str(peer["_id"])},
    )
    assert conversation_res.status_code == 200, conversation_res.text
    conversation_id = conversation_res.json()["data"]["id"]

    contacts_res = await inprocess_client.get(
        "/pings/contacts",
        headers={"Authorization": f"Bearer {user_tokens['access_token']}"},
    )
    assert contacts_res.status_code == 200, contacts_res.text

    contacts = contacts_res.json()["data"]
    assert len(contacts) == 1
    assert contacts[0]["peer"]["id"] == str(peer["_id"])
    assert contacts[0]["conversation_id"] == conversation_id


@pytest.mark.asyncio
async def test_blocked_users_are_excluded_from_discovery_search(inprocess_client):
    viewer, viewer_tokens = await _create_verified_user_and_tokens(
        f"search-viewer-{uuid.uuid4().hex[:8]}@test.com"
    )
    target, target_tokens = await _create_verified_user_and_tokens(
        f"search-target-{uuid.uuid4().hex[:8]}@test.com"
    )

    block_res = await inprocess_client.post(
        "/pings/block",
        headers={"Authorization": f"Bearer {target_tokens['access_token']}"},
        json={"peer_user_id": str(viewer["_id"])},
    )
    assert block_res.status_code == 200, block_res.text

    search_res = await inprocess_client.get(
        "/discovery/users/search",
        headers={"Authorization": f"Bearer {viewer_tokens['access_token']}"},
        params={"q": target["username"][:6]},
    )
    assert search_res.status_code == 200, search_res.text
    assert all(item["id"] != str(target["_id"]) for item in search_res.json()["data"])


@pytest.mark.asyncio
async def test_block_prevents_direct_message_send(inprocess_client):
    sender, sender_tokens = await _create_verified_user_and_tokens(
        f"block-sender-{uuid.uuid4().hex[:8]}@test.com"
    )
    target, target_tokens = await _create_verified_user_and_tokens(
        f"block-target-{uuid.uuid4().hex[:8]}@test.com"
    )
    await _grant_chat_permission(str(sender["_id"]), str(target["_id"]))

    conversation_res = await inprocess_client.post(
        "/conversations",
        headers={"Authorization": f"Bearer {sender_tokens['access_token']}"},
        json={"peer_user_id": str(target["_id"])},
    )
    assert conversation_res.status_code == 200, conversation_res.text
    conversation_id = conversation_res.json()["data"]["id"]

    block_res = await inprocess_client.post(
        "/pings/block",
        headers={"Authorization": f"Bearer {target_tokens['access_token']}"},
        json={"peer_user_id": str(sender["_id"])},
    )
    assert block_res.status_code == 200, block_res.text

    message_res = await inprocess_client.post(
        f"/conversations/{conversation_id}/messages/text",
        headers={"Authorization": f"Bearer {sender_tokens['access_token']}"},
        json={"text": "hello"},
    )
    assert message_res.status_code == 403, message_res.text
    assert message_res.json()["error"]["code"] == "CHAT_BLOCKED"
