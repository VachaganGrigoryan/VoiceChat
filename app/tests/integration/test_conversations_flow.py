from __future__ import annotations

import pytest

from app.db.models import ConversationDocument, RelationshipDocument
from app.modules.conversations.repository.helpers import dm_key_for
from app.tests.integration.test_realtime_socket import (
    _create_verified_user_and_tokens,
    _grant_chat_permission,
)


def _auth(access_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {access_token}"}


async def _pair(sender_email: str, receiver_email: str):
    sender, sender_tokens = await _create_verified_user_and_tokens(sender_email)
    receiver, receiver_tokens = await _create_verified_user_and_tokens(receiver_email)
    await _grant_chat_permission(str(sender["_id"]), str(receiver["_id"]))
    return sender, sender_tokens, receiver, receiver_tokens


@pytest.mark.asyncio
async def test_create_or_get_dm_is_idempotent(inprocess_client):
    sender, sender_tokens, receiver, _ = await _pair(
        "conv-a1@test.com", "conv-b1@test.com"
    )

    first = await inprocess_client.post(
        "/conversations",
        json={"peer_user_id": str(receiver["_id"])},
        headers=_auth(sender_tokens["access_token"]),
    )
    second = await inprocess_client.post(
        "/conversations",
        json={"peer_user_id": str(receiver["_id"])},
        headers=_auth(sender_tokens["access_token"]),
    )

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    first_data = first.json()["data"]
    assert first_data["id"] == second.json()["data"]["id"]
    assert first_data["type"] == "dm"
    assert first_data["encryption"] == "none"
    assert sorted(first_data["participant_ids"]) == sorted(
        [str(sender["_id"]), str(receiver["_id"])]
    )
    assert first_data["peer_user"]["id"] == str(receiver["_id"])
    assert first_data["peer_user"]["username"] == receiver["username"]
    assert first_data["peer_user"]["chat_allowed"] is True
    assert first_data["peer_user"]["connection_status"] == "active"
    assert {item["id"] for item in first_data["participant_users"]} == {
        str(sender["_id"]),
        str(receiver["_id"]),
    }


@pytest.mark.asyncio
async def test_dm_only_rejects_self(inprocess_client):
    sender, sender_tokens = await _create_verified_user_and_tokens("conv-self@test.com")

    resp = await inprocess_client.post(
        "/conversations",
        json={"peer_user_id": str(sender["_id"])},
        headers=_auth(sender_tokens["access_token"]),
    )

    assert resp.status_code == 400, resp.text
    assert resp.json()["error"]["code"] == "INVALID_CONVERSATION"


@pytest.mark.asyncio
async def test_send_materializes_conversation_and_unread(inprocess_client):
    sender, sender_tokens, receiver, receiver_tokens = await _pair(
        "conv-a2@test.com", "conv-b2@test.com"
    )

    conv = await inprocess_client.post(
        "/conversations",
        json={"peer_user_id": str(receiver["_id"])},
        headers=_auth(sender_tokens["access_token"]),
    )
    conversation_id = conv.json()["data"]["id"]
    send = await inprocess_client.post(
        f"/messages/conversation/{conversation_id}/text",
        json={"text": "hello there"},
        headers=_auth(sender_tokens["access_token"]),
    )
    assert send.status_code == 201, send.text

    # Receiver inbox: conversation materialized, one unread, preview populated.
    receiver_inbox = await inprocess_client.get(
        "/conversations", headers=_auth(receiver_tokens["access_token"])
    )
    assert receiver_inbox.status_code == 200, receiver_inbox.text
    rows = receiver_inbox.json()["data"]
    assert len(rows) == 1
    row = rows[0]
    assert row["unread_count"] == 1
    assert row["last_message_preview"]["text"] == "hello there"
    assert row["id"] is not None
    assert row["type"] == "dm"
    assert row["encryption"] == "none"
    assert str(sender["_id"]) in row["participant_ids"]
    assert row["peer_user"]["id"] == str(sender["_id"])
    assert row["peer_user"]["username"] == sender["username"]

    # Sender inbox: sender is caught up on their own message.
    sender_inbox = await inprocess_client.get(
        "/conversations", headers=_auth(sender_tokens["access_token"])
    )
    assert sender_inbox.json()["data"][0]["unread_count"] == 0


@pytest.mark.asyncio
async def test_create_group_returns_participant_user_summaries(inprocess_client):
    owner, owner_tokens = await _create_verified_user_and_tokens(
        "conv-group-owner@test.com"
    )
    member, _ = await _create_verified_user_and_tokens("conv-group-member@test.com")
    await _grant_chat_permission(str(owner["_id"]), str(member["_id"]))

    resp = await inprocess_client.post(
        "/conversations/groups",
        json={"title": "Project chat", "participant_ids": [str(member["_id"])]},
        headers=_auth(owner_tokens["access_token"]),
    )

    assert resp.status_code == 201, resp.text
    data = resp.json()["data"]
    assert data["type"] == "group"
    assert data["title"] == "Project chat"
    assert data["peer_user"] is None
    assert {item["id"] for item in data["participant_users"]} == {
        str(owner["_id"]),
        str(member["_id"]),
    }


@pytest.mark.asyncio
async def test_conversation_scoped_send_and_list_with_content_envelope(
    inprocess_client,
):
    sender, sender_tokens, receiver, receiver_tokens = await _pair(
        "conv-a3@test.com", "conv-b3@test.com"
    )

    conv = await inprocess_client.post(
        "/conversations",
        json={"peer_user_id": str(receiver["_id"])},
        headers=_auth(sender_tokens["access_token"]),
    )
    conversation_id = conv.json()["data"]["id"]

    send = await inprocess_client.post(
        f"/messages/conversation/{conversation_id}/text",
        json={"text": "scoped hello"},
        headers=_auth(sender_tokens["access_token"]),
    )
    assert send.status_code == 201, send.text
    message = send.json()["data"]
    assert message["content"]["encryption"] == "none"
    assert message["content"]["type"] == "text"
    assert message["content"]["plaintext"]["text"] == "scoped hello"
    assert "text" not in message

    listing = await inprocess_client.get(
        f"/messages/conversation/{conversation_id}",
        headers=_auth(receiver_tokens["access_token"]),
    )
    assert listing.status_code == 200, listing.text
    items = listing.json()["data"]
    assert len(items) == 1
    assert items[0]["content"]["plaintext"]["text"] == "scoped hello"


@pytest.mark.asyncio
async def test_send_text_resolves_mentions_against_conversation_participants(
    inprocess_client,
):
    sender, sender_tokens, receiver, _ = await _pair(
        "conv-mention-a@test.com", "conv-mention-b@test.com"
    )

    conv = await inprocess_client.post(
        "/conversations",
        json={"peer_user_id": str(receiver["_id"])},
        headers=_auth(sender_tokens["access_token"]),
    )
    conversation_id = conv.json()["data"]["id"]

    send = await inprocess_client.post(
        f"/messages/conversation/{conversation_id}/text",
        json={"text": f"hello @{receiver['username']} and @all"},
        headers=_auth(sender_tokens["access_token"]),
    )

    assert send.status_code == 201, send.text
    message = send.json()["data"]
    assert message["mention_user_ids"] == [str(receiver["_id"])]
    assert message["mention_scope"] == "all"


@pytest.mark.asyncio
async def test_mark_read_zeroes_unread(inprocess_client):
    sender, sender_tokens, receiver, receiver_tokens = await _pair(
        "conv-a4@test.com", "conv-b4@test.com"
    )

    conv = await inprocess_client.post(
        "/conversations",
        json={"peer_user_id": str(receiver["_id"])},
        headers=_auth(sender_tokens["access_token"]),
    )
    conversation_id = conv.json()["data"]["id"]
    await inprocess_client.post(
        f"/messages/conversation/{conversation_id}/text",
        json={"text": "unread me"},
        headers=_auth(sender_tokens["access_token"]),
    )

    inbox = await inprocess_client.get(
        "/conversations", headers=_auth(receiver_tokens["access_token"])
    )
    assert inbox.json()["data"][0]["unread_count"] == 1

    read = await inprocess_client.post(
        f"/conversations/{conversation_id}/read",
        headers=_auth(receiver_tokens["access_token"]),
    )
    assert read.status_code == 204, read.text

    after = await inprocess_client.get(
        "/conversations", headers=_auth(receiver_tokens["access_token"])
    )
    assert after.json()["data"][0]["unread_count"] == 0


@pytest.mark.asyncio
async def test_call_materializes_conversation_entity(inprocess_client):
    caller, caller_tokens, callee, callee_tokens = await _pair(
        "conv-call-a@test.com", "conv-call-b@test.com"
    )

    create = await inprocess_client.post(
        "/calls",
        json={"callee_user_id": str(callee["_id"]), "type": "audio"},
        headers=_auth(caller_tokens["access_token"]),
    )
    assert create.status_code == 201, create.text
    call_id = create.json()["data"]["call"]["id"]

    reject = await inprocess_client.post(
        f"/calls/{call_id}/reject",
        headers=_auth(callee_tokens["access_token"]),
    )
    assert reject.status_code == 200, reject.text

    # A call-only DM must now have a first-class conversation entity + participants.
    dm_key = dm_key_for(str(caller["_id"]), str(callee["_id"]))
    conversation = await ConversationDocument.find_one({"type": "dm", "dm_key": dm_key})
    assert conversation is not None
    participants = await RelationshipDocument.find(
        {
            "kind": "membership",
            "target_type": "conversation",
            "target_id": conversation.str_id,
            "status": "active",
        }
    ).to_list()
    assert len(participants) == 2


@pytest.mark.asyncio
async def test_edit_keeps_content_envelope_in_sync(inprocess_client):
    sender, sender_tokens, receiver, _ = await _pair(
        "conv-edit-a@test.com", "conv-edit-b@test.com"
    )

    conv = await inprocess_client.post(
        "/conversations",
        json={"peer_user_id": str(receiver["_id"])},
        headers=_auth(sender_tokens["access_token"]),
    )
    conversation_id = conv.json()["data"]["id"]
    send = await inprocess_client.post(
        f"/messages/conversation/{conversation_id}/text",
        json={"text": "original"},
        headers=_auth(sender_tokens["access_token"]),
    )
    message_id = send.json()["data"]["id"]

    edit = await inprocess_client.patch(
        f"/messages/{message_id}",
        json={"text": "edited"},
        headers=_auth(sender_tokens["access_token"]),
    )
    assert edit.status_code == 200, edit.text
    body = edit.json()["data"]
    assert "text" not in body
    assert body["content"]["plaintext"]["text"] == "edited"


@pytest.mark.asyncio
async def test_conversation_send_requires_membership(inprocess_client):
    sender, sender_tokens, receiver, _ = await _pair(
        "conv-a5@test.com", "conv-b5@test.com"
    )
    outsider, outsider_tokens = await _create_verified_user_and_tokens(
        "conv-out@test.com"
    )

    conv = await inprocess_client.post(
        "/conversations",
        json={"peer_user_id": str(receiver["_id"])},
        headers=_auth(sender_tokens["access_token"]),
    )
    conversation_id = conv.json()["data"]["id"]

    resp = await inprocess_client.get(
        f"/messages/conversation/{conversation_id}",
        headers=_auth(outsider_tokens["access_token"]),
    )
    assert resp.status_code == 404, resp.text
    assert resp.json()["error"]["code"] == "CONVERSATION_NOT_FOUND"
