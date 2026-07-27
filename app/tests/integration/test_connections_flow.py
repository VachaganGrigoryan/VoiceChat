from __future__ import annotations

import uuid

import pytest

from app.tests.integration.test_realtime_socket import (
    _create_verified_user_and_tokens,
)


@pytest.mark.asyncio
async def test_connection_lifecycle_and_block_cutover(inprocess_client):
    sender, sender_tokens = await _create_verified_user_and_tokens(
        f"connection-sender-{uuid.uuid4().hex[:8]}@test.com"
    )
    receiver, receiver_tokens = await _create_verified_user_and_tokens(
        f"connection-receiver-{uuid.uuid4().hex[:8]}@test.com"
    )
    sender_headers = {
        "Authorization": f"Bearer {sender_tokens['access_token']}"
    }
    receiver_headers = {
        "Authorization": f"Bearer {receiver_tokens['access_token']}"
    }

    request_res = await inprocess_client.post(
        f"/connections/{receiver['_id']}/ping",
        headers=sender_headers,
    )
    assert request_res.status_code == 201, request_res.text
    relationship_id = request_res.json()["data"]["id"]

    duplicate_res = await inprocess_client.post(
        f"/connections/{receiver['_id']}/ping",
        headers=sender_headers,
    )
    assert duplicate_res.status_code == 201, duplicate_res.text
    assert duplicate_res.json()["data"]["id"] == relationship_id

    incoming_res = await inprocess_client.get(
        "/connections/pending",
        headers=receiver_headers,
        params={"direction": "incoming"},
    )
    assert incoming_res.status_code == 200, incoming_res.text
    incoming = incoming_res.json()["data"]
    assert len(incoming) == 1
    assert incoming[0]["relationship"]["id"] == relationship_id
    assert incoming[0]["peer"]["id"] == str(sender["_id"])
    assert incoming[0]["direction"] == "incoming"

    accept_res = await inprocess_client.post(
        f"/connections/{relationship_id}/accept",
        headers=receiver_headers,
    )
    assert accept_res.status_code == 200, accept_res.text
    assert accept_res.json()["data"]["status"] == "active"

    contacts_res = await inprocess_client.get(
        "/connections",
        headers=sender_headers,
    )
    assert contacts_res.status_code == 200, contacts_res.text
    assert contacts_res.json()["data"][0]["relationship"]["id"] == relationship_id

    conversation_res = await inprocess_client.post(
        "/conversations",
        headers=sender_headers,
        json={"peer_user_id": str(receiver["_id"])},
    )
    assert conversation_res.status_code == 200, conversation_res.text
    conversation_id = conversation_res.json()["data"]["id"]

    delete_chat_res = await inprocess_client.delete(
        f"/conversations/{conversation_id}",
        headers=sender_headers,
    )
    assert delete_chat_res.status_code == 200, delete_chat_res.text

    contacts_after_delete_res = await inprocess_client.get(
        "/connections",
        headers=sender_headers,
    )
    assert contacts_after_delete_res.status_code == 200
    assert (
        contacts_after_delete_res.json()["data"][0]["relationship"]["status"]
        == "active"
    )

    block_res = await inprocess_client.post(
        f"/blocks/{sender['_id']}",
        headers=receiver_headers,
    )
    assert block_res.status_code == 201, block_res.text
    assert block_res.json()["data"]["blocked_id"] == str(sender["_id"])

    contacts_after_block_res = await inprocess_client.get(
        "/connections",
        headers=sender_headers,
    )
    assert contacts_after_block_res.status_code == 200
    assert contacts_after_block_res.json()["data"] == []

    unblock_res = await inprocess_client.delete(
        f"/blocks/{sender['_id']}",
        headers=receiver_headers,
    )
    assert unblock_res.status_code == 204, unblock_res.text

    contacts_after_unblock_res = await inprocess_client.get(
        "/connections",
        headers=sender_headers,
    )
    assert contacts_after_unblock_res.status_code == 200
    assert contacts_after_unblock_res.json()["data"] == []

    rerequest_res = await inprocess_client.post(
        f"/connections/{receiver['_id']}/ping",
        headers=sender_headers,
    )
    assert rerequest_res.status_code == 201, rerequest_res.text
    assert rerequest_res.json()["data"]["target_id"] == str(receiver["_id"])
