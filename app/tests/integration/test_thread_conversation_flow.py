from __future__ import annotations

import pytest

from app.tests.integration.test_realtime_socket import (
    _create_verified_user_and_tokens,
    _grant_chat_permission,
)


def _auth(access_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {access_token}"}


# `core-resource-model` narrows Conversation to dm|group: a thread is message topology on
# the parent conversation, not a sub-conversation. The thread behavior this file used to
# cover (idempotent open, independent read state, separate inbox listing, convert-to-group)
# re-lands in `unified-messages` against the message container model. What remains here is
# the narrowed write contract.


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
async def test_thread_conversation_creation_is_rejected(inprocess_client):
    a, a_tokens, b, b_tokens, conversation_id, message_id = await _dm_with_message(
        inprocess_client, "thr-a1@test.com", "thr-b1@test.com"
    )

    opened = await inprocess_client.post(
        f"/conversations/{conversation_id}/messages/{message_id}/thread-conversation",
        headers=_auth(b_tokens["access_token"]),
    )
    assert opened.status_code == 400, opened.text
    assert opened.json()["error"]["code"] == "INVALID_CONVERSATION_TYPE"


@pytest.mark.asyncio
async def test_no_thread_conversation_is_persisted(inprocess_client):
    """The rejected request must not leave a type=thread row behind."""
    a, a_tokens, b, b_tokens, conversation_id, message_id = await _dm_with_message(
        inprocess_client, "thr-a2@test.com", "thr-b2@test.com"
    )

    await inprocess_client.post(
        f"/conversations/{conversation_id}/messages/{message_id}/thread-conversation",
        headers=_auth(b_tokens["access_token"]),
    )

    from app.db.models import ConversationDocument

    assert await ConversationDocument.find({"type": "thread"}).count() == 0


@pytest.mark.asyncio
async def test_parent_dm_is_unaffected(inprocess_client):
    """The parent DM keeps working after a rejected thread open."""
    a, a_tokens, b, b_tokens, conversation_id, message_id = await _dm_with_message(
        inprocess_client, "thr-a3@test.com", "thr-b3@test.com"
    )

    await inprocess_client.post(
        f"/conversations/{conversation_id}/messages/{message_id}/thread-conversation",
        headers=_auth(b_tokens["access_token"]),
    )

    reply = await inprocess_client.post(
        f"/conversations/{conversation_id}/messages/text",
        json={"text": "inline reply instead"},
        headers=_auth(b_tokens["access_token"]),
    )
    assert reply.status_code == 201, reply.text

    single = await inprocess_client.get(
        f"/conversations/{conversation_id}",
        headers=_auth(a_tokens["access_token"]),
    )
    assert single.status_code == 200, single.text
    assert single.json()["data"]["type"] == "dm"
