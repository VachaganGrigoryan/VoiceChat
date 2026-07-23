from __future__ import annotations

import pytest

from app.tests.integration.test_realtime_socket import (
    _create_verified_user_and_tokens,
    _grant_chat_permission,
)


def _auth(access_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {access_token}"}


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
async def test_open_thread_yields_sub_conversation(inprocess_client):
    a, a_tokens, b, b_tokens, conversation_id, message_id = await _dm_with_message(
        inprocess_client, "thr-a1@test.com", "thr-b1@test.com"
    )

    opened = await inprocess_client.post(
        f"/conversations/{conversation_id}/messages/{message_id}/thread-conversation",
        headers=_auth(b_tokens["access_token"]),
    )
    assert opened.status_code == 200, opened.text
    thread = opened.json()["data"]
    assert thread["type"] == "thread"
    assert thread["parent_conversation_id"] == conversation_id
    assert thread["root_message_id"] == message_id
    assert str(b["_id"]) in thread["participant_ids"]
    assert str(a["_id"]) in thread["participant_ids"]


@pytest.mark.asyncio
async def test_open_thread_is_idempotent(inprocess_client):
    a, a_tokens, b, b_tokens, conversation_id, message_id = await _dm_with_message(
        inprocess_client, "thr-a2@test.com", "thr-b2@test.com"
    )

    first = await inprocess_client.post(
        f"/conversations/{conversation_id}/messages/{message_id}/thread-conversation",
        headers=_auth(a_tokens["access_token"]),
    )
    second = await inprocess_client.post(
        f"/conversations/{conversation_id}/messages/{message_id}/thread-conversation",
        headers=_auth(b_tokens["access_token"]),
    )
    assert first.status_code == 200 and second.status_code == 200
    assert first.json()["data"]["id"] == second.json()["data"]["id"]


@pytest.mark.asyncio
async def test_threads_are_listed_separately_from_main_inbox(inprocess_client):
    _a, _a_tokens, _b, b_tokens, conversation_id, message_id = await _dm_with_message(
        inprocess_client, "thr-a-list@test.com", "thr-b-list@test.com"
    )

    opened = await inprocess_client.post(
        f"/conversations/{conversation_id}/messages/{message_id}/thread-conversation",
        headers=_auth(b_tokens["access_token"]),
    )
    assert opened.status_code == 200, opened.text
    thread_id = opened.json()["data"]["id"]

    inbox = await inprocess_client.get(
        "/conversations", headers=_auth(b_tokens["access_token"])
    )
    assert all(row["type"] != "thread" for row in inbox.json()["data"])

    threads = await inprocess_client.get(
        "/conversations/threads", headers=_auth(b_tokens["access_token"])
    )
    assert threads.status_code == 200, threads.text
    row = next(item for item in threads.json()["data"] if item["thread"]["id"] == thread_id)
    assert row["parent"]["id"] == conversation_id
    assert row["root_message"]["id"] == message_id


@pytest.mark.asyncio
async def test_thread_has_independent_read_state(inprocess_client):
    a, a_tokens, b, b_tokens, conversation_id, message_id = await _dm_with_message(
        inprocess_client, "thr-a3@test.com", "thr-b3@test.com"
    )

    # b opens the thread; a (root-message sender) is seeded as a participant too.
    thread = await inprocess_client.post(
        f"/conversations/{conversation_id}/messages/{message_id}/thread-conversation",
        headers=_auth(b_tokens["access_token"]),
    )
    thread_id = thread.json()["data"]["id"]

    # A reply inside the thread creates unread for the other participant.
    reply = await inprocess_client.post(
        f"/conversations/{thread_id}/messages/text",
        json={"text": "thread reply"},
        headers=_auth(a_tokens["access_token"]),
    )
    assert reply.status_code == 201, reply.text

    b_inbox = await inprocess_client.get(
        "/conversations/threads", headers=_auth(b_tokens["access_token"])
    )
    thread_row = next(
        row for row in b_inbox.json()["data"] if row["thread"]["id"] == thread_id
    )
    assert thread_row["thread"]["unread_count"] == 1

    # Reading the thread does not touch the parent conversation's read state.
    read = await inprocess_client.post(
        f"/conversations/{thread_id}/read",
        headers=_auth(b_tokens["access_token"]),
    )
    assert read.status_code == 204, read.text

    b_inbox_after = await inprocess_client.get(
        "/conversations/threads", headers=_auth(b_tokens["access_token"])
    )
    thread_after = next(
        row for row in b_inbox_after.json()["data"] if row["thread"]["id"] == thread_id
    )
    assert thread_after["thread"]["unread_count"] == 0
    parent_inbox_after = await inprocess_client.get(
        "/conversations", headers=_auth(b_tokens["access_token"])
    )
    parent_after = next(
        row for row in parent_inbox_after.json()["data"] if row["id"] == conversation_id
    )
    assert parent_after["unread_count"] == 1


@pytest.mark.asyncio
async def test_thread_requires_parent_membership(inprocess_client):
    a, a_tokens, b, b_tokens, conversation_id, message_id = await _dm_with_message(
        inprocess_client, "thr-a4@test.com", "thr-b4@test.com"
    )
    outsider, outsider_tokens = await _create_verified_user_and_tokens(
        "thr-x4@test.com"
    )

    resp = await inprocess_client.post(
        f"/conversations/{conversation_id}/messages/{message_id}/thread-conversation",
        headers=_auth(outsider_tokens["access_token"]),
    )
    assert resp.status_code == 404, resp.text


@pytest.mark.asyncio
async def test_thread_owner_can_convert_and_lock_thread(inprocess_client):
    a, a_tokens, b, b_tokens, conversation_id, message_id = await _dm_with_message(
        inprocess_client, "thr-a-convert@test.com", "thr-b-convert@test.com"
    )

    opened = await inprocess_client.post(
        f"/conversations/{conversation_id}/messages/{message_id}/thread-conversation",
        headers=_auth(a_tokens["access_token"]),
    )
    assert opened.status_code == 200, opened.text
    thread_id = opened.json()["data"]["id"]
    followed = await inprocess_client.post(
        f"/conversations/{conversation_id}/messages/{message_id}/thread-conversation",
        headers=_auth(b_tokens["access_token"]),
    )
    assert followed.status_code == 200, followed.text

    reply = await inprocess_client.post(
        f"/conversations/{thread_id}/messages/text",
        json={"text": "canonical thread reply"},
        headers=_auth(b_tokens["access_token"]),
    )
    assert reply.status_code == 201, reply.text

    forbidden = await inprocess_client.post(
        f"/conversations/threads/{thread_id}/convert-to-group",
        json={
            "title": "Extracted thread",
            "participant_ids": [str(a["_id"]), str(b["_id"])],
        },
        headers=_auth(b_tokens["access_token"]),
    )
    assert forbidden.status_code == 403, forbidden.text

    converted = await inprocess_client.post(
        f"/conversations/threads/{thread_id}/convert-to-group",
        json={
            "title": "Extracted thread",
            "participant_ids": [str(a["_id"]), str(b["_id"])],
        },
        headers=_auth(a_tokens["access_token"]),
    )
    assert converted.status_code == 201, converted.text
    data = converted.json()["data"]
    group_id = data["group"]["id"]
    assert data["thread"]["settings"]["converted_to_conversation_id"] == group_id
    assert data["imported_count"] >= 2

    locked_send = await inprocess_client.post(
        f"/conversations/{thread_id}/messages/text",
        json={"text": "after lock"},
        headers=_auth(a_tokens["access_token"]),
    )
    assert locked_send.status_code == 409, locked_send.text
