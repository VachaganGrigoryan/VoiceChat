from __future__ import annotations

import pytest

from app.factory import create_app
from app.tests.integration.test_realtime_socket import (
    _create_verified_user_and_tokens,
    _grant_chat_permission,
)


def _auth(access_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {access_token}"}


def test_legacy_message_paths_are_absent_from_openapi() -> None:
    spec = create_app().openapi()

    legacy_paths = [
        path
        for path in spec["paths"]
        if path == "/messages" or path.startswith("/messages/")
    ]

    assert legacy_paths == []


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
        f"/conversations/{conversation_id}/messages/{root_message['id']}/thread",
        headers=_auth(sender_tokens["access_token"]),
    )
    assert thread.status_code == 200, thread.text
    thread_items = thread.json()["data"]
    assert [item["content"]["plaintext"]["text"] for item in thread_items] == [
        "thread reply"
    ]

    reaction = await inprocess_client.post(
        f"/conversations/{conversation_id}/messages/{root_message['id']}/reactions",
        json={"emoji": "fire"},
        headers=_auth(receiver_tokens["access_token"]),
    )
    assert reaction.status_code == 200, reaction.text
    assert reaction.json()["data"]["reactions"][0]["emoji"] == "fire"
