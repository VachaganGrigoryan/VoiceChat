from __future__ import annotations

import pytest

from app.tests.integration.test_realtime_socket import (
    _create_verified_user_and_tokens,
)


def _auth(access_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {access_token}"}


async def _channel(client, owner_tokens: dict, title: str) -> str:
    created = await client.post(
        "/conversations/channels",
        json={"title": title, "posting_policy": "everyone"},
        headers=_auth(owner_tokens["access_token"]),
    )
    assert created.status_code == 201, created.text
    return created.json()["data"]["id"]


def _ids(resp) -> list[str]:
    return [row["id"] for row in resp.json()["data"]]


@pytest.mark.asyncio
async def test_pin_moves_conversation_to_top_for_caller_only(inprocess_client):
    owner, owner_tokens = await _create_verified_user_and_tokens("inbox-o1@test.com")
    older = await _channel(inprocess_client, owner_tokens, "Older")
    newer = await _channel(inprocess_client, owner_tokens, "Newer")

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
    keep = await _channel(inprocess_client, owner_tokens, "Keep")
    stash = await _channel(inprocess_client, owner_tokens, "Stash")

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
    work = await _channel(inprocess_client, owner_tokens, "Work")
    personal = await _channel(inprocess_client, owner_tokens, "Personal")

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
async def test_inbox_state_requires_membership(inprocess_client):
    owner, owner_tokens = await _create_verified_user_and_tokens("inbox-o4@test.com")
    outsider, outsider_tokens = await _create_verified_user_and_tokens("inbox-x4@test.com")
    channel_id = await _channel(inprocess_client, owner_tokens, "Private")

    resp = await inprocess_client.patch(
        f"/conversations/{channel_id}/inbox",
        json={"pinned": True},
        headers=_auth(outsider_tokens["access_token"]),
    )
    assert resp.status_code == 404, resp.text
