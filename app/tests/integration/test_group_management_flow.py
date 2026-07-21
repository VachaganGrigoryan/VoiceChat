from __future__ import annotations

import pytest

from app.tests.integration.test_realtime_socket import (
    _create_verified_user_and_tokens,
    _grant_chat_permission,
)

_PNG_BYTES = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR-fake-image-body"


def _auth(access_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {access_token}"}


async def _group(inprocess_client, owner_email: str, *member_emails: str):
    """Create an owner + members and a group they all belong to."""
    owner, owner_tokens = await _create_verified_user_and_tokens(owner_email)
    members: list[tuple[dict, dict]] = []
    for email in member_emails:
        member, member_tokens = await _create_verified_user_and_tokens(email)
        await _grant_chat_permission(str(owner["_id"]), str(member["_id"]))
        members.append((member, member_tokens))

    resp = await inprocess_client.post(
        "/conversations/groups",
        json={
            "title": "Group",
            "participant_ids": [str(m["_id"]) for m, _ in members],
        },
        headers=_auth(owner_tokens["access_token"]),
    )
    assert resp.status_code == 201, resp.text
    conversation_id = resp.json()["data"]["id"]
    return owner, owner_tokens, members, conversation_id


async def _list_message_texts(inprocess_client, conversation_id, tokens):
    resp = await inprocess_client.get(
        f"/conversations/{conversation_id}/messages",
        headers=_auth(tokens["access_token"]),
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


@pytest.mark.asyncio
async def test_owner_can_rename_group(inprocess_client):
    owner, owner_tokens, _members, conversation_id = await _group(
        inprocess_client, "grp-rn-owner@test.com", "grp-rn-m1@test.com"
    )

    resp = await inprocess_client.patch(
        f"/conversations/groups/{conversation_id}",
        json={"title": "Renamed group"},
        headers=_auth(owner_tokens["access_token"]),
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["title"] == "Renamed group"


@pytest.mark.asyncio
async def test_member_cannot_rename_group(inprocess_client):
    _owner, _owner_tokens, members, conversation_id = await _group(
        inprocess_client, "grp-rn2-owner@test.com", "grp-rn2-m1@test.com"
    )
    _member, member_tokens = members[0]

    resp = await inprocess_client.patch(
        f"/conversations/groups/{conversation_id}",
        json={"title": "Sneaky rename"},
        headers=_auth(member_tokens["access_token"]),
    )

    assert resp.status_code == 403, resp.text
    assert resp.json()["error"]["code"] == "CONVERSATION_FORBIDDEN"


@pytest.mark.asyncio
async def test_admin_can_rename_group(inprocess_client):
    owner, owner_tokens, members, conversation_id = await _group(
        inprocess_client, "grp-rn3-owner@test.com", "grp-rn3-m1@test.com"
    )
    member, member_tokens = members[0]

    promote = await inprocess_client.patch(
        f"/conversations/{conversation_id}/members/{member['_id']}/role",
        json={"role": "admin"},
        headers=_auth(owner_tokens["access_token"]),
    )
    assert promote.status_code == 200, promote.text

    resp = await inprocess_client.patch(
        f"/conversations/groups/{conversation_id}",
        json={"title": "Admin renamed"},
        headers=_auth(member_tokens["access_token"]),
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["title"] == "Admin renamed"


@pytest.mark.asyncio
async def test_owner_can_set_and_remove_group_avatar(inprocess_client):
    owner, owner_tokens, _members, conversation_id = await _group(
        inprocess_client, "grp-av-owner@test.com", "grp-av-m1@test.com"
    )

    set_resp = await inprocess_client.patch(
        f"/conversations/groups/{conversation_id}/avatar",
        files={"file": ("group.png", _PNG_BYTES, "image/png")},
        headers=_auth(owner_tokens["access_token"]),
    )
    assert set_resp.status_code == 200, set_resp.text
    image = set_resp.json()["data"]["image"]
    assert image is not None
    assert image["key"]
    assert image["url"]

    remove_resp = await inprocess_client.delete(
        f"/conversations/groups/{conversation_id}/avatar",
        headers=_auth(owner_tokens["access_token"]),
    )
    assert remove_resp.status_code == 200, remove_resp.text
    assert remove_resp.json()["data"]["image"] is None


@pytest.mark.asyncio
async def test_member_cannot_set_group_avatar(inprocess_client):
    _owner, _owner_tokens, members, conversation_id = await _group(
        inprocess_client, "grp-av2-owner@test.com", "grp-av2-m1@test.com"
    )
    _member, member_tokens = members[0]

    resp = await inprocess_client.patch(
        f"/conversations/groups/{conversation_id}/avatar",
        files={"file": ("group.png", _PNG_BYTES, "image/png")},
        headers=_auth(member_tokens["access_token"]),
    )

    assert resp.status_code == 403, resp.text
    assert resp.json()["error"]["code"] == "CONVERSATION_FORBIDDEN"


@pytest.mark.asyncio
async def test_owner_clears_history_for_everyone(inprocess_client):
    owner, owner_tokens, members, conversation_id = await _group(
        inprocess_client, "grp-cl-owner@test.com", "grp-cl-m1@test.com"
    )
    _member, member_tokens = members[0]

    for tokens, text in ((owner_tokens, "owner msg"), (member_tokens, "member msg")):
        send = await inprocess_client.post(
            f"/conversations/{conversation_id}/messages/text",
            json={"text": text},
            headers=_auth(tokens["access_token"]),
        )
        assert send.status_code == 201, send.text

    clear = await inprocess_client.delete(
        f"/conversations/{conversation_id}/messages/all",
        headers=_auth(owner_tokens["access_token"]),
    )
    assert clear.status_code == 200, clear.text
    assert clear.json()["data"]["cleared_count"] == 2

    assert await _list_message_texts(inprocess_client, conversation_id, owner_tokens) == []
    assert (
        await _list_message_texts(inprocess_client, conversation_id, member_tokens) == []
    )


@pytest.mark.asyncio
async def test_member_cannot_clear_history_for_everyone(inprocess_client):
    _owner, _owner_tokens, members, conversation_id = await _group(
        inprocess_client, "grp-cl2-owner@test.com", "grp-cl2-m1@test.com"
    )
    _member, member_tokens = members[0]

    resp = await inprocess_client.delete(
        f"/conversations/{conversation_id}/messages/all",
        headers=_auth(member_tokens["access_token"]),
    )

    assert resp.status_code == 403, resp.text
    assert resp.json()["error"]["code"] == "CONVERSATION_FORBIDDEN"


@pytest.mark.asyncio
async def test_per_user_clear_leaves_history_for_others(inprocess_client):
    owner, owner_tokens, members, conversation_id = await _group(
        inprocess_client, "grp-cl3-owner@test.com", "grp-cl3-m1@test.com"
    )
    _member, member_tokens = members[0]

    send = await inprocess_client.post(
        f"/conversations/{conversation_id}/messages/text",
        json={"text": "owner keeps this"},
        headers=_auth(owner_tokens["access_token"]),
    )
    assert send.status_code == 201, send.text

    clear = await inprocess_client.delete(
        f"/conversations/{conversation_id}/messages",
        headers=_auth(member_tokens["access_token"]),
    )
    assert clear.status_code == 200, clear.text

    # Member's own view is cleared, but the owner still sees their message.
    assert (
        await _list_message_texts(inprocess_client, conversation_id, member_tokens) == []
    )
    owner_msgs = await _list_message_texts(
        inprocess_client, conversation_id, owner_tokens
    )
    assert len(owner_msgs) == 1
