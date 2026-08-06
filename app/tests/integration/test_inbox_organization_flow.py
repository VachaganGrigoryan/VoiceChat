from __future__ import annotations

import pytest

from app.tests.integration.test_realtime_socket import (
    _create_verified_user_and_tokens,
    _grant_chat_permission,
)


def _auth(access_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {access_token}"}


async def _group(client, owner: dict, owner_tokens: dict, title: str) -> str:
    """Create a group the owner belongs to, as an inbox row to organize.

    These scenarios only need *a* conversation; they used to use channels, which are no
    longer a conversation type. A group needs a second participant, so each call mints a
    throwaway peer (titles are unique per test) and grants the chat permission the ping
    gate requires.
    """
    peer, _ = await _create_verified_user_and_tokens(f"peer-{title.lower()}@test.com")
    await _grant_chat_permission(str(owner["_id"]), str(peer["_id"]))
    created = await client.post(
        "/conversations/groups",
        json={"title": title, "participant_ids": [str(peer["_id"])]},
        headers=_auth(owner_tokens["access_token"]),
    )
    assert created.status_code == 201, created.text
    return created.json()["data"]["id"]


def _ids(resp) -> list[str]:
    return [row["id"] for row in resp.json()["data"]]


@pytest.mark.asyncio
async def test_pin_moves_conversation_to_top_for_caller_only(inprocess_client):
    owner, owner_tokens = await _create_verified_user_and_tokens("inbox-o1@test.com")
    older = await _group(inprocess_client, owner, owner_tokens, "Older")
    newer = await _group(inprocess_client, owner, owner_tokens, "Newer")

    # Default order: most-recently created/active first (newer before older).
    default = await inprocess_client.get(
        "/conversations", headers=_auth(owner_tokens["access_token"])
    )
    assert _ids(default) == [newer, older]

    # Pin the older one -> it jumps to the top of the caller's list.
    pin = await inprocess_client.patch(
        f"/conversations/{older}/inbox",
        json={"pinned": True},
        headers=_auth(owner_tokens["access_token"]),
    )
    assert pin.status_code == 200, pin.text
    assert pin.json()["data"]["pinned"] is True

    pinned_first = await inprocess_client.get(
        "/conversations", headers=_auth(owner_tokens["access_token"])
    )
    assert _ids(pinned_first) == [older, newer]
    # Viewer-relative flags are surfaced on the inbox row.
    rows = {row["id"]: row for row in pinned_first.json()["data"]}
    assert rows[older]["pinned"] is True
    assert rows[newer]["pinned"] is False


@pytest.mark.asyncio
async def test_archive_separates_conversation(inprocess_client):
    owner, owner_tokens = await _create_verified_user_and_tokens("inbox-o2@test.com")
    keep = await _group(inprocess_client, owner, owner_tokens, "Keep")
    stash = await _group(inprocess_client, owner, owner_tokens, "Stash")

    archive = await inprocess_client.patch(
        f"/conversations/{stash}/inbox",
        json={"archived": True},
        headers=_auth(owner_tokens["access_token"]),
    )
    assert archive.status_code == 200, archive.text

    default = await inprocess_client.get(
        "/conversations", headers=_auth(owner_tokens["access_token"])
    )
    assert _ids(default) == [keep]

    archived = await inprocess_client.get(
        "/conversations?archived=true", headers=_auth(owner_tokens["access_token"])
    )
    assert _ids(archived) == [stash]


@pytest.mark.asyncio
async def test_folder_filters_listing(inprocess_client):
    owner, owner_tokens = await _create_verified_user_and_tokens("inbox-o3@test.com")
    work = await _group(inprocess_client, owner, owner_tokens, "Work")
    personal = await _group(inprocess_client, owner, owner_tokens, "Personal")

    await inprocess_client.patch(
        f"/conversations/{work}/inbox",
        json={"folder": "work"},
        headers=_auth(owner_tokens["access_token"]),
    )

    filtered = await inprocess_client.get(
        "/conversations?folder=work", headers=_auth(owner_tokens["access_token"])
    )
    assert _ids(filtered) == [work]

    # Clearing the folder (explicit null) removes it from the folder view.
    await inprocess_client.patch(
        f"/conversations/{work}/inbox",
        json={"folder": None},
        headers=_auth(owner_tokens["access_token"]),
    )
    cleared = await inprocess_client.get(
        "/conversations?folder=work", headers=_auth(owner_tokens["access_token"])
    )
    assert _ids(cleared) == []
    del personal


@pytest.mark.asyncio
async def test_get_single_conversation_returns_archived_view(inprocess_client):
    owner, owner_tokens = await _create_verified_user_and_tokens("inbox-g1@test.com")
    stash = await _group(inprocess_client, owner, owner_tokens, "Stash")

    await inprocess_client.patch(
        f"/conversations/{stash}/inbox",
        json={"archived": True, "folder": "later"},
        headers=_auth(owner_tokens["access_token"]),
    )

    # Archived conversations are gone from the default inbox page but still
    # fully resolvable by id, with the caller's inbox flags on the view.
    single = await inprocess_client.get(
        f"/conversations/{stash}", headers=_auth(owner_tokens["access_token"])
    )
    assert single.status_code == 200, single.text
    data = single.json()["data"]
    assert data["id"] == stash
    assert data["archived"] is True
    assert data["folder"] == "later"


@pytest.mark.asyncio
async def test_get_single_conversation_requires_membership(inprocess_client):
    owner, owner_tokens = await _create_verified_user_and_tokens("inbox-g2@test.com")
    _outsider, outsider_tokens = await _create_verified_user_and_tokens(
        "inbox-gx2@test.com"
    )
    conversation_id = await _group(inprocess_client, owner, owner_tokens, "Private")

    resp = await inprocess_client.get(
        f"/conversations/{conversation_id}",
        headers=_auth(outsider_tokens["access_token"]),
    )
    assert resp.status_code == 404, resp.text


@pytest.mark.asyncio
async def test_folder_crud(inprocess_client):
    owner, owner_tokens = await _create_verified_user_and_tokens("inbox-f1@test.com")
    work_a = await _group(inprocess_client, owner, owner_tokens, "WorkA")
    work_b = await _group(inprocess_client, owner, owner_tokens, "WorkB")

    for cid in (work_a, work_b):
        await inprocess_client.patch(
            f"/conversations/{cid}/inbox",
            json={"folder": "work"},
            headers=_auth(owner_tokens["access_token"]),
        )
    # Archive one so we prove folder discovery survives archiving.
    await inprocess_client.patch(
        f"/conversations/{work_b}/inbox",
        json={"archived": True},
        headers=_auth(owner_tokens["access_token"]),
    )

    listed = await inprocess_client.get(
        "/conversations/folders", headers=_auth(owner_tokens["access_token"])
    )
    assert listed.status_code == 200, listed.text
    folders = {row["name"]: row for row in listed.json()["data"]}
    assert folders["work"]["count"] == 2
    assert folders["work"]["archived_count"] == 1

    # Rename across all conversations.
    renamed = await inprocess_client.patch(
        "/conversations/folders/work",
        json={"new_name": "office"},
        headers=_auth(owner_tokens["access_token"]),
    )
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["data"]["updated"] == 2
    after_rename = await inprocess_client.get(
        "/conversations/folders", headers=_auth(owner_tokens["access_token"])
    )
    names = {row["name"] for row in after_rename.json()["data"]}
    assert names == {"office"}

    # Delete clears the label from every conversation.
    deleted = await inprocess_client.delete(
        "/conversations/folders/office", headers=_auth(owner_tokens["access_token"])
    )
    assert deleted.status_code == 204, deleted.text
    emptied = await inprocess_client.get(
        "/conversations/folders", headers=_auth(owner_tokens["access_token"])
    )
    assert emptied.json()["data"] == []


@pytest.mark.asyncio
async def test_bulk_inbox_state(inprocess_client):
    owner, owner_tokens = await _create_verified_user_and_tokens("inbox-b1@test.com")
    one = await _group(inprocess_client, owner, owner_tokens, "One")
    two = await _group(inprocess_client, owner, owner_tokens, "Two")

    resp = await inprocess_client.patch(
        "/conversations/inbox",
        json={"conversation_ids": [one, two], "archived": True, "folder": "bulk"},
        headers=_auth(owner_tokens["access_token"]),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["updated"] == 2

    archived = await inprocess_client.get(
        "/conversations?archived=true", headers=_auth(owner_tokens["access_token"])
    )
    assert set(_ids(archived)) == {one, two}


@pytest.mark.asyncio
async def test_reply_resurfaces_archived_but_incoming_does_not(inprocess_client):
    sender, sender_tokens = await _create_verified_user_and_tokens("inbox-r1@test.com")
    receiver, receiver_tokens = await _create_verified_user_and_tokens(
        "inbox-r2@test.com"
    )
    sender_id = str(sender["_id"])
    receiver_id = str(receiver["_id"])
    await _grant_chat_permission(sender_id, receiver_id)

    dm = await inprocess_client.post(
        "/conversations",
        json={"peer_user_id": receiver_id},
        headers=_auth(sender_tokens["access_token"]),
    )
    assert dm.status_code == 200, dm.text
    conversation_id = dm.json()["data"]["id"]

    # Sender archives the DM.
    await inprocess_client.patch(
        f"/conversations/{conversation_id}/inbox",
        json={"archived": True},
        headers=_auth(sender_tokens["access_token"]),
    )

    # An incoming message from the receiver must NOT unarchive the sender's view.
    incoming = await inprocess_client.post(
        f"/conversations/{conversation_id}/messages/text",
        json={"text": "hi"},
        headers=_auth(receiver_tokens["access_token"]),
    )
    assert incoming.status_code == 201, incoming.text
    still_archived = await inprocess_client.get(
        f"/conversations/{conversation_id}",
        headers=_auth(sender_tokens["access_token"]),
    )
    assert still_archived.json()["data"]["archived"] is True

    # The sender's own reply resurfaces the conversation for them.
    reply = await inprocess_client.post(
        f"/conversations/{conversation_id}/messages/text",
        json={"text": "back"},
        headers=_auth(sender_tokens["access_token"]),
    )
    assert reply.status_code == 201, reply.text
    resurfaced = await inprocess_client.get(
        f"/conversations/{conversation_id}",
        headers=_auth(sender_tokens["access_token"]),
    )
    assert resurfaced.json()["data"]["archived"] is False


@pytest.mark.asyncio
async def test_inbox_state_requires_membership(inprocess_client):
    owner, owner_tokens = await _create_verified_user_and_tokens("inbox-o4@test.com")
    outsider, outsider_tokens = await _create_verified_user_and_tokens("inbox-x4@test.com")
    conversation_id = await _group(inprocess_client, owner, owner_tokens, "Private")

    resp = await inprocess_client.patch(
        f"/conversations/{conversation_id}/inbox",
        json={"pinned": True},
        headers=_auth(outsider_tokens["access_token"]),
    )
    assert resp.status_code == 404, resp.text
