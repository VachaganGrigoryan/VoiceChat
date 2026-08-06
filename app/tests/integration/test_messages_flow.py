from __future__ import annotations

import pytest

from app.factory import create_app
from app.tests.integration.test_realtime_socket import (
    _create_verified_user_and_tokens,
    _grant_chat_permission,
)


def _auth(access_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {access_token}"}


def test_legacy_nested_item_paths_are_absent_from_openapi() -> None:
    spec = create_app().openapi()

    nested_item_paths = [
        path
        for path in spec["paths"]
        if path.startswith("/conversations/") and "/messages/{" in path
    ]

    assert nested_item_paths == []


@pytest.mark.asyncio
async def test_conversation_scoped_message_thread_and_reaction_flow(
    inprocess_client,
):
    sender, sender_tokens = await _create_verified_user_and_tokens(
        "messages-flow-sender@test.com"
    )
    receiver, receiver_tokens = await _create_verified_user_and_tokens(
        "messages-flow-receiver@test.com"
    )
    sender_id = str(sender["_id"])
    receiver_id = str(receiver["_id"])
    await _grant_chat_permission(sender_id, receiver_id)

    conversation = await inprocess_client.post(
        "/conversations",
        json={"peer_user_id": receiver_id},
        headers=_auth(sender_tokens["access_token"]),
    )
    assert conversation.status_code == 200, conversation.text
    conversation_id = conversation.json()["data"]["id"]

    root = await inprocess_client.post(
        f"/conversations/{conversation_id}/messages/text",
        json={"text": "root"},
        headers=_auth(sender_tokens["access_token"]),
    )
    assert root.status_code == 201, root.text
    root_message = root.json()["data"]
    assert root_message["conversation_id"] == conversation_id
    assert root_message["content"]["plaintext"]["text"] == "root"
    assert "receiver_id" not in root_message
    assert "text" not in root_message

    reply = await inprocess_client.post(
        f"/conversations/{conversation_id}/messages/text",
        json={
            "text": "thread reply",
            "reply_mode": "thread",
            "reply_to_message_id": root_message["id"],
        },
        headers=_auth(receiver_tokens["access_token"]),
    )
    assert reply.status_code == 201, reply.text

    thread = await inprocess_client.get(
        f"/messages/{root_message['id']}/thread",
        headers=_auth(sender_tokens["access_token"]),
    )
    assert thread.status_code == 200, thread.text
    thread_items = thread.json()["data"]
    assert [item["content"]["plaintext"]["text"] for item in thread_items] == [
        "thread reply"
    ]

    reaction = await inprocess_client.post(
        f"/messages/{root_message['id']}/reactions",
        json={"emoji": "fire"},
        headers=_auth(receiver_tokens["access_token"]),
    )
    assert reaction.status_code == 200, reaction.text
    assert reaction.json()["data"]["reactions"][0]["emoji"] == "fire"


@pytest.mark.asyncio
async def test_non_participant_message_access_denied_with_403(inprocess_client):
    sender, sender_tokens = await _create_verified_user_and_tokens(
        "msg-gate-sender@test.com"
    )
    receiver, _ = await _create_verified_user_and_tokens(
        "msg-gate-receiver@test.com"
    )
    outsider, outsider_tokens = await _create_verified_user_and_tokens(
        "msg-gate-outsider@test.com"
    )
    await _grant_chat_permission(str(sender["_id"]), str(receiver["_id"]))

    conversation = await inprocess_client.post(
        "/conversations",
        json={"peer_user_id": str(receiver["_id"])},
        headers=_auth(sender_tokens["access_token"]),
    )
    assert conversation.status_code == 200, conversation.text
    conversation_id = conversation.json()["data"]["id"]

    root = await inprocess_client.post(
        f"/conversations/{conversation_id}/messages/text",
        json={"text": "private message"},
        headers=_auth(sender_tokens["access_token"]),
    )
    assert root.status_code == 201, root.text
    msg_id = root.json()["data"]["id"]

    # Non-participant gets 403 FORBIDDEN on flat item endpoints.
    get_res = await inprocess_client.get(
        f"/messages/{msg_id}",
        headers=_auth(outsider_tokens["access_token"]),
    )
    assert get_res.status_code == 403, get_res.text
    assert get_res.json()["error"]["code"] == "FORBIDDEN"

    patch_res = await inprocess_client.patch(
        f"/messages/{msg_id}",
        json={"text": "hacked"},
        headers=_auth(outsider_tokens["access_token"]),
    )
    assert patch_res.status_code == 403, patch_res.text

    react_res = await inprocess_client.post(
        f"/messages/{msg_id}/reactions",
        json={"emoji": "thumbsup"},
        headers=_auth(outsider_tokens["access_token"]),
    )
    assert react_res.status_code == 403, react_res.text


@pytest.mark.asyncio
async def test_channel_message_flat_route_operations_and_pin_rejection(inprocess_client):
    owner, owner_tokens = await _create_verified_user_and_tokens("chan-flat-owner@test.com")

    channel = await inprocess_client.post(
        "/channels",
        json={"name": "Announcements", "slug": "announcements"},
        headers=_auth(owner_tokens["access_token"]),
    )
    assert channel.status_code == 201, channel.text
    channel_id = channel.json()["data"]["id"]

    post = await inprocess_client.post(
        f"/channels/{channel_id}/messages",
        json={"text": "Channel Announcement"},
        headers=_auth(owner_tokens["access_token"]),
    )
    assert post.status_code == 201, post.text
    msg_id = post.json()["data"]["id"]

    # React to channel post via /messages/{message_id}/reactions
    reaction = await inprocess_client.post(
        f"/messages/{msg_id}/reactions",
        json={"emoji": "rocket"},
        headers=_auth(owner_tokens["access_token"]),
    )
    assert reaction.status_code == 200, reaction.text

    # Edit channel post via /messages/{message_id}
    edited = await inprocess_client.patch(
        f"/messages/{msg_id}",
        json={"text": "Updated Announcement"},
        headers=_auth(owner_tokens["access_token"]),
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["data"]["content"]["plaintext"]["text"] == "Updated Announcement"

    # Mark read via /messages/{message_id}/read
    read_res = await inprocess_client.post(
        f"/messages/{msg_id}/read",
        headers=_auth(owner_tokens["access_token"]),
    )
    assert read_res.status_code == 200, read_res.text

    # Get thread via /messages/{message_id}/thread
    thread_res = await inprocess_client.get(
        f"/messages/{msg_id}/thread",
        headers=_auth(owner_tokens["access_token"]),
    )
    assert thread_res.status_code == 200, thread_res.text

    # Pin via /messages/{message_id}/pin — a channel tracks its pinned set too
    pin_res = await inprocess_client.post(
        f"/messages/{msg_id}/pin",
        headers=_auth(owner_tokens["access_token"]),
    )
    assert pin_res.status_code == 200, pin_res.text
    pinned = pin_res.json()["data"]
    assert pinned["container_type"] == "channel"
    assert msg_id in pinned["pinned_message_ids"]

    unpin_res = await inprocess_client.delete(
        f"/messages/{msg_id}/pin",
        headers=_auth(owner_tokens["access_token"]),
    )
    assert unpin_res.status_code == 200, unpin_res.text
    assert msg_id not in unpin_res.json()["data"]["pinned_message_ids"]

    # Delete channel post via /messages/{message_id}
    del_res = await inprocess_client.delete(
        f"/messages/{msg_id}",
        headers=_auth(owner_tokens["access_token"]),
    )
    assert del_res.status_code == 200, del_res.text

