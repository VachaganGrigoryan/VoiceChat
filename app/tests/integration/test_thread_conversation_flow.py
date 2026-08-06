from __future__ import annotations

import pytest

from app.tests.integration.test_realtime_socket import (
    _create_verified_user_and_tokens,
    _grant_chat_permission,
)


def _auth(access_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {access_token}"}


# `unified-messages` removes `Conversation(type=thread)` entirely: a thread is flat
# message topology on the parent container (`thread_root_id`), never a sub-conversation.
# These tests pin that contract end to end.


async def _dm_with_message(client, a_email: str, b_email: str):
    a, a_tokens = await _create_verified_user_and_tokens(a_email)
    b, b_tokens = await _create_verified_user_and_tokens(b_email)
    await _grant_chat_permission(str(a["_id"]), str(b["_id"]))
    conv = await client.post(
        "/conversations",
        json={"peer_user_id": str(b["_id"])},
        headers=_auth(a_tokens["access_token"]),
    )
    conversation_id = conv.json()["data"]["id"]
    send = await client.post(
        f"/conversations/{conversation_id}/messages/text",
        json={"text": "root message"},
        headers=_auth(a_tokens["access_token"]),
    )
    message_id = send.json()["data"]["id"]
    return (a, a_tokens, b, b_tokens, conversation_id, message_id)


@pytest.mark.asyncio
async def test_thread_reply_does_not_create_a_conversation(inprocess_client):
    a, a_tokens, b, b_tokens, conversation_id, message_id = await _dm_with_message(
        inprocess_client, "thr-a1@test.com", "thr-b1@test.com"
    )

    reply = await inprocess_client.post(
        f"/conversations/{conversation_id}/messages/text",
        json={
            "text": "thread reply",
            "reply_mode": "thread",
            "reply_to_message_id": message_id,
        },
        headers=_auth(b_tokens["access_token"]),
    )
    assert reply.status_code == 201, reply.text
    body = reply.json()["data"]
    assert body["thread_root_id"] == message_id
    assert body["container_type"] == "conversation"
    assert body["container_id"] == conversation_id

    from app.db.models import ConversationDocument

    assert await ConversationDocument.find({"type": "thread"}).count() == 0


@pytest.mark.asyncio
async def test_thread_items_share_the_root_container(inprocess_client):
    a, a_tokens, b, b_tokens, conversation_id, message_id = await _dm_with_message(
        inprocess_client, "thr-a2@test.com", "thr-b2@test.com"
    )

    for text, author in (("first", b_tokens), ("second", a_tokens)):
        created = await inprocess_client.post(
            f"/conversations/{conversation_id}/messages/text",
            json={
                "text": text,
                "reply_mode": "thread",
                "reply_to_message_id": message_id,
            },
            headers=_auth(author["access_token"]),
        )
        assert created.status_code == 201, created.text

    thread = await inprocess_client.get(
        f"/messages/{message_id}/thread",
        headers=_auth(a_tokens["access_token"]),
    )
    assert thread.status_code == 200, thread.text
    items = thread.json()["data"]
    assert [item["content"]["plaintext"]["text"] for item in items] == [
        "first",
        "second",
    ]
    assert all(item["container_type"] == "conversation" for item in items)
    assert all(item["container_id"] == conversation_id for item in items)
    assert all(item["thread_root_id"] == message_id for item in items)

    summary = await inprocess_client.get(
        f"/messages/{message_id}/thread-summary",
        headers=_auth(a_tokens["access_token"]),
    )
    assert summary.status_code == 200, summary.text
    assert summary.json()["data"]["thread_reply_count"] == 2


@pytest.mark.asyncio
async def test_thread_conversation_endpoint_is_gone(inprocess_client):
    """The old `thread-conversation` open endpoint no longer exists."""
    a, a_tokens, b, b_tokens, conversation_id, message_id = await _dm_with_message(
        inprocess_client, "thr-a3@test.com", "thr-b3@test.com"
    )

    opened = await inprocess_client.post(
        f"/conversations/{conversation_id}/messages/{message_id}/thread-conversation",
        headers=_auth(b_tokens["access_token"]),
    )
    assert opened.status_code == 404, opened.text
