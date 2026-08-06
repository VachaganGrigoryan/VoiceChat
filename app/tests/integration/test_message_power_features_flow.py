from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from bson import ObjectId

from app.db.mongo import get_db
from app.modules.messages.dependencies import get_messages_service
from app.tests.integration.test_realtime_socket import (
    _create_verified_user_and_tokens,
    _grant_chat_permission,
)


def _auth(access_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {access_token}"}


async def _dm(sender_email: str, receiver_email: str, client):
    sender, sender_tokens = await _create_verified_user_and_tokens(sender_email)
    receiver, receiver_tokens = await _create_verified_user_and_tokens(receiver_email)
    await _grant_chat_permission(str(sender["_id"]), str(receiver["_id"]))
    conversation = await client.post(
        "/conversations",
        json={"peer_user_id": str(receiver["_id"])},
        headers=_auth(sender_tokens["access_token"]),
    )
    assert conversation.status_code == 200, conversation.text
    return (
        sender,
        sender_tokens,
        receiver,
        receiver_tokens,
        conversation.json()["data"]["id"],
    )


async def _send_text(client, conversation_id, tokens, text):
    resp = await client.post(
        f"/messages/conversation/{conversation_id}/text",
        json={"text": text},
        headers=_auth(tokens["access_token"]),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]


@pytest.mark.asyncio
async def test_pin_authorization_and_listing(inprocess_client):
    owner, owner_tokens = await _create_verified_user_and_tokens("pin-owner@test.com")
    member, member_tokens = await _create_verified_user_and_tokens(
        "pin-member@test.com"
    )
    await _grant_chat_permission(str(owner["_id"]), str(member["_id"]))

    group = await inprocess_client.post(
        "/conversations/groups",
        json={"title": "Pins", "participant_ids": [str(member["_id"])]},
        headers=_auth(owner_tokens["access_token"]),
    )
    assert group.status_code == 201, group.text
    conversation_id = group.json()["data"]["id"]

    message = await _send_text(
        inprocess_client, conversation_id, owner_tokens, "pin me"
    )

    # A plain member holds no pin right.
    forbidden = await inprocess_client.post(
        f"/messages/{message['id']}/pin",
        headers=_auth(member_tokens["access_token"]),
    )
    assert forbidden.status_code == 403, forbidden.text

    # The owner may pin.
    pinned = await inprocess_client.post(
        f"/messages/{message['id']}/pin",
        headers=_auth(owner_tokens["access_token"]),
    )
    assert pinned.status_code == 200, pinned.text
    assert message["id"] in pinned.json()["data"]["pinned_message_ids"]

    listing = await inprocess_client.get(
        f"/messages/conversation/{conversation_id}/pinned",
        headers=_auth(member_tokens["access_token"]),
    )
    assert listing.status_code == 200, listing.text
    assert [item["id"] for item in listing.json()["data"]] == [message["id"]]

    unpinned = await inprocess_client.delete(
        f"/messages/{message['id']}/pin",
        headers=_auth(owner_tokens["access_token"]),
    )
    assert unpinned.status_code == 200, unpinned.text
    assert unpinned.json()["data"]["pinned_message_ids"] == []


@pytest.mark.asyncio
async def test_dm_participants_can_pin_and_unpin_messages(inprocess_client):
    _sender, sender_tokens, _receiver, receiver_tokens, conversation_id = await _dm(
        "pin-dm-a@test.com", "pin-dm-b@test.com", inprocess_client
    )
    message = await _send_text(
        inprocess_client, conversation_id, sender_tokens, "pin in dm"
    )

    pinned = await inprocess_client.post(
        f"/messages/{message['id']}/pin",
        headers=_auth(receiver_tokens["access_token"]),
    )
    assert pinned.status_code == 200, pinned.text
    assert message["id"] in pinned.json()["data"]["pinned_message_ids"]

    listing = await inprocess_client.get(
        f"/messages/conversation/{conversation_id}/pinned",
        headers=_auth(sender_tokens["access_token"]),
    )
    assert listing.status_code == 200, listing.text
    assert [item["id"] for item in listing.json()["data"]] == [message["id"]]

    unpinned = await inprocess_client.delete(
        f"/messages/{message['id']}/pin",
        headers=_auth(receiver_tokens["access_token"]),
    )
    assert unpinned.status_code == 200, unpinned.text
    assert unpinned.json()["data"]["pinned_message_ids"] == []


@pytest.mark.asyncio
async def test_forward_message_carries_origin_header(inprocess_client):
    sender, sender_tokens, receiver, receiver_tokens, source_id = await _dm(
        "fwd-a@test.com", "fwd-b@test.com", inprocess_client
    )
    # A second conversation the sender also participates in.
    third, _ = await _create_verified_user_and_tokens("fwd-c@test.com")
    await _grant_chat_permission(str(sender["_id"]), str(third["_id"]))
    target = await inprocess_client.post(
        "/conversations",
        json={"peer_user_id": str(third["_id"])},
        headers=_auth(sender_tokens["access_token"]),
    )
    target_id = target.json()["data"]["id"]

    message = await _send_text(
        inprocess_client, source_id, sender_tokens, "original text"
    )

    forwarded = await inprocess_client.post(
        f"/messages/{message['id']}/forward",
        json={"target_conversation_id": target_id},
        headers=_auth(sender_tokens["access_token"]),
    )
    assert forwarded.status_code == 201, forwarded.text
    data = forwarded.json()["data"]
    assert data["conversation_id"] == target_id
    assert data["content"]["plaintext"]["text"] == "original text"
    assert data["forwarded_from"]["conversation_id"] == source_id
    assert data["forwarded_from"]["message_id"] == message["id"]
    assert data["forwarded_from"]["sender_id"] == str(sender["_id"])


@pytest.mark.asyncio
async def test_edit_history_retains_prior_versions(inprocess_client):
    sender, sender_tokens, receiver, receiver_tokens, conversation_id = await _dm(
        "edit-a@test.com", "edit-b@test.com", inprocess_client
    )
    message = await _send_text(inprocess_client, conversation_id, sender_tokens, "v1")

    for text in ("v2", "v3"):
        edited = await inprocess_client.patch(
            f"/messages/{message['id']}",
            json={"text": text},
            headers=_auth(sender_tokens["access_token"]),
        )
        assert edited.status_code == 200, edited.text

    final = edited.json()["data"]
    assert final["content"]["plaintext"]["text"] == "v3"
    prior_texts = [
        item["content"]["plaintext"]["text"] for item in final["edit_history"]
    ]
    assert prior_texts == ["v1", "v2"]


@pytest.mark.asyncio
async def test_scheduled_message_withheld_then_released_and_cancelled(inprocess_client):
    sender, sender_tokens, receiver, receiver_tokens, conversation_id = await _dm(
        "sched-a@test.com", "sched-b@test.com", inprocess_client
    )

    scheduled = await inprocess_client.post(
        f"/messages/conversation/{conversation_id}/schedule",
        json={
            "text": "future message",
            "scheduled_for": (
                datetime.now(UTC) + timedelta(hours=1)
            ).isoformat(),
        },
        headers=_auth(sender_tokens["access_token"]),
    )
    assert scheduled.status_code == 201, scheduled.text
    scheduled_id = scheduled.json()["data"]["id"]
    assert scheduled.json()["data"]["state"] == "scheduled"

    # Withheld from the timeline.
    timeline = await inprocess_client.get(
        f"/messages/conversation/{conversation_id}",
        headers=_auth(receiver_tokens["access_token"]),
    )
    assert scheduled_id not in [item["id"] for item in timeline.json()["data"]]

    # Visible in the sender's scheduled list.
    listing = await inprocess_client.get(
        f"/messages/conversation/{conversation_id}/scheduled",
        headers=_auth(sender_tokens["access_token"]),
    )
    assert [item["id"] for item in listing.json()["data"]] == [scheduled_id]

    # Force the send time into the past and release via the worker path.
    await get_db()["messages"].update_one(
        {"_id": ObjectId(scheduled_id)},
        {"$set": {"scheduled_for": datetime.now(UTC) - timedelta(minutes=1)}},
    )
    released = await get_messages_service().release_due_scheduled_messages()
    assert [item.result.message.id for item in released] == [scheduled_id]
    # Fan-out targets are resolved so the worker can deliver realtime.
    assert set(released[0].participant_ids) == {
        str(sender["_id"]),
        str(receiver["_id"]),
    }

    timeline_after = await inprocess_client.get(
        f"/messages/conversation/{conversation_id}",
        headers=_auth(receiver_tokens["access_token"]),
    )
    released_item = next(
        item
        for item in timeline_after.json()["data"]
        if item["id"] == scheduled_id
    )
    assert released_item["state"] == "sent"


@pytest.mark.asyncio
async def test_scheduled_message_cancel_before_send(inprocess_client):
    sender, sender_tokens, receiver, receiver_tokens, conversation_id = await _dm(
        "sched-cancel-a@test.com", "sched-cancel-b@test.com", inprocess_client
    )
    scheduled = await inprocess_client.post(
        f"/messages/conversation/{conversation_id}/schedule",
        json={
            "text": "never sent",
            "scheduled_for": (datetime.now(UTC) + timedelta(hours=2)).isoformat(),
        },
        headers=_auth(sender_tokens["access_token"]),
    )
    scheduled_id = scheduled.json()["data"]["id"]

    cancelled = await inprocess_client.delete(
        f"/messages/{scheduled_id}/scheduled",
        headers=_auth(sender_tokens["access_token"]),
    )
    assert cancelled.status_code == 204, cancelled.text

    listing = await inprocess_client.get(
        f"/messages/conversation/{conversation_id}/scheduled",
        headers=_auth(sender_tokens["access_token"]),
    )
    assert listing.json()["data"] == []


@pytest.mark.asyncio
async def test_draft_sync_and_cleared_on_send(inprocess_client):
    sender, sender_tokens, receiver, receiver_tokens, conversation_id = await _dm(
        "draft-a@test.com", "draft-b@test.com", inprocess_client
    )

    saved = await inprocess_client.put(
        f"/conversations/{conversation_id}/draft",
        json={"text": "half-written"},
        headers=_auth(sender_tokens["access_token"]),
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["data"]["draft_text"] == "half-written"

    # Draft is retrievable (as from another device).
    fetched = await inprocess_client.get(
        f"/conversations/{conversation_id}/draft",
        headers=_auth(sender_tokens["access_token"]),
    )
    assert fetched.json()["data"]["draft_text"] == "half-written"

    # Sending a message clears the draft.
    await _send_text(inprocess_client, conversation_id, sender_tokens, "sent for real")
    after = await inprocess_client.get(
        f"/conversations/{conversation_id}/draft",
        headers=_auth(sender_tokens["access_token"]),
    )
    assert after.json()["data"]["draft_text"] is None


@pytest.mark.asyncio
async def test_saved_messages_are_private_to_the_user(inprocess_client):
    sender, sender_tokens, receiver, receiver_tokens, conversation_id = await _dm(
        "saved-a@test.com", "saved-b@test.com", inprocess_client
    )
    message = await _send_text(
        inprocess_client, conversation_id, sender_tokens, "bookmark this"
    )

    saved = await inprocess_client.post(
        "/me/saved-messages",
        json={
            "container_type": "conversation",
            "container_id": conversation_id,
            "message_id": message["id"],
        },
        headers=_auth(receiver_tokens["access_token"]),
    )
    assert saved.status_code == 201, saved.text

    mine = await inprocess_client.get(
        "/me/saved-messages",
        headers=_auth(receiver_tokens["access_token"]),
    )
    assert [item["message_id"] for item in mine.json()["data"]] == [message["id"]]

    # The sender cannot observe the receiver's private save.
    others = await inprocess_client.get(
        "/me/saved-messages",
        headers=_auth(sender_tokens["access_token"]),
    )
    assert others.json()["data"] == []

    removed = await inprocess_client.delete(
        f"/me/saved-messages/{message['id']}",
        headers=_auth(receiver_tokens["access_token"]),
    )
    assert removed.status_code == 204, removed.text


@pytest.mark.asyncio
async def test_message_search_respects_visibility(inprocess_client):
    sender, sender_tokens, receiver, receiver_tokens, conversation_id = await _dm(
        "search-a@test.com", "search-b@test.com", inprocess_client
    )
    await _send_text(inprocess_client, conversation_id, sender_tokens, "quantum banana")

    # A stranger with no access to the conversation gets no results.
    stranger, stranger_tokens = await _create_verified_user_and_tokens(
        "search-stranger@test.com"
    )

    found = await inprocess_client.get(
        "/search/messages",
        params={"q": "banana"},
        headers=_auth(receiver_tokens["access_token"]),
    )
    assert found.status_code == 200, found.text
    assert any(
        item["content"]["plaintext"]["text"] == "quantum banana"
        for item in found.json()["data"]
    )

    empty = await inprocess_client.get(
        "/search/messages",
        params={"q": "banana"},
        headers=_auth(stranger_tokens["access_token"]),
    )
    assert empty.json()["data"] == []


@pytest.mark.asyncio
async def test_message_search_paginates_by_cursor_and_is_stable_across_an_insertion(
    inprocess_client,
):
    sender, sender_tokens, receiver, receiver_tokens, conversation_id = await _dm(
        "search-page-a@test.com", "search-page-b@test.com", inprocess_client
    )
    for index in range(4):
        await _send_text(
            inprocess_client, conversation_id, sender_tokens, f"mango-page-{index}"
        )

    first = await inprocess_client.get(
        "/search/messages",
        params={"q": "mango-page", "limit": 2},
        headers=_auth(receiver_tokens["access_token"]),
    )
    assert first.status_code == 200, first.text
    page_one = [item["id"] for item in first.json()["data"]]
    cursor = first.json()["meta"]["next_cursor"]
    assert cursor

    # A new match arrives between the two page requests.
    await _send_text(
        inprocess_client, conversation_id, sender_tokens, "mango-page-new"
    )

    second = await inprocess_client.get(
        "/search/messages",
        params={"q": "mango-page", "limit": 2, "cursor": cursor},
        headers=_auth(receiver_tokens["access_token"]),
    )
    assert second.status_code == 200, second.text
    page_two = [item["id"] for item in second.json()["data"]]
    assert not set(page_one) & set(page_two), (page_one, page_two)
