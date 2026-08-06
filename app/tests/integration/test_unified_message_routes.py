"""The container-addressed message routes, exercised against both container types.

The claim this change makes is that one route family serves a conversation and a
channel alike, so every operation here is asserted twice -- a conversation-only
pass would miss exactly the regression the unification is meant to prevent.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.tests.integration.test_realtime_socket import (
    _create_verified_user_and_tokens,
    _grant_chat_permission,
)

_PNG_BYTES = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR-fake-image-body"


def _auth(access_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {access_token}"}


async def _group(client, owner_tokens, member_id: str) -> str:
    created = await client.post(
        "/conversations/groups",
        json={"title": "Unified", "participant_ids": [member_id]},
        headers=_auth(owner_tokens["access_token"]),
    )
    assert created.status_code == 201, created.text
    return created.json()["data"]["id"]


async def _channel(
    client, owner_tokens, *, slug: str, visibility: str = "public"
) -> str:
    created = await client.post(
        "/channels",
        json={
            "name": f"Unified {slug}",
            "kind": "announcement",
            "visibility": visibility,
            "posting_policy": "owner",
            "comment_policy": "everyone",
            "slug": slug,
        },
        headers=_auth(owner_tokens["access_token"]),
    )
    assert created.status_code == 201, created.text
    return created.json()["data"]["id"]


async def _send_text(client, container_type, container_id, tokens, text: str) -> dict:
    resp = await client.post(
        f"/messages/{container_type}/{container_id}/text",
        json={"text": text},
        headers=_auth(tokens["access_token"]),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]


@pytest.mark.asyncio
@pytest.mark.parametrize("container_type", ["conversation", "channel"])
async def test_send_and_list_through_the_container_routes(
    inprocess_client, container_type
):
    owner, owner_tokens = await _create_verified_user_and_tokens(
        f"unified-send-{container_type}@test.com"
    )
    member, _member_tokens = await _create_verified_user_and_tokens(
        f"unified-send-peer-{container_type}@test.com"
    )
    await _grant_chat_permission(str(owner["_id"]), str(member["_id"]))

    if container_type == "conversation":
        container_id = await _group(inprocess_client, owner_tokens, str(member["_id"]))
    else:
        container_id = await _channel(
            inprocess_client, owner_tokens, slug=f"send-{container_type}"
        )

    text = await _send_text(
        inprocess_client, container_type, container_id, owner_tokens, "hello"
    )
    assert text["container_type"] == container_type
    assert text["container_id"] == container_id

    content = await inprocess_client.post(
        f"/messages/{container_type}/{container_id}/content",
        json={"type": "sticker", "sticker": {"pack": "core", "name": "wave"}},
        headers=_auth(owner_tokens["access_token"]),
    )
    assert content.status_code == 201, content.text

    media = await inprocess_client.post(
        f"/messages/{container_type}/{container_id}/media",
        data={"type": "media", "media_kind": "image"},
        files={"file": ("shot.png", _PNG_BYTES, "image/png")},
        headers=_auth(owner_tokens["access_token"]),
    )
    assert media.status_code == 201, media.text

    history = await inprocess_client.get(
        f"/messages/{container_type}/{container_id}",
        headers=_auth(owner_tokens["access_token"]),
    )
    assert history.status_code == 200, history.text
    items = history.json()["data"]
    assert len(items) == 3
    assert {item["container_type"] for item in items} == {container_type}


@pytest.mark.asyncio
@pytest.mark.parametrize("container_type", ["conversation", "channel"])
async def test_schedule_list_and_cancel_for_both_containers(
    inprocess_client, container_type
):
    owner, owner_tokens = await _create_verified_user_and_tokens(
        f"unified-sched-{container_type}@test.com"
    )
    member, _member_tokens = await _create_verified_user_and_tokens(
        f"unified-sched-peer-{container_type}@test.com"
    )
    await _grant_chat_permission(str(owner["_id"]), str(member["_id"]))

    if container_type == "conversation":
        container_id = await _group(inprocess_client, owner_tokens, str(member["_id"]))
    else:
        container_id = await _channel(
            inprocess_client, owner_tokens, slug=f"sched-{container_type}"
        )

    scheduled_for = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    created = await inprocess_client.post(
        f"/messages/{container_type}/{container_id}/schedule",
        json={"text": "later", "scheduled_for": scheduled_for},
        headers=_auth(owner_tokens["access_token"]),
    )
    assert created.status_code == 201, created.text
    message_id = created.json()["data"]["id"]

    listed = await inprocess_client.get(
        f"/messages/{container_type}/{container_id}/scheduled",
        headers=_auth(owner_tokens["access_token"]),
    )
    assert listed.status_code == 200, listed.text
    assert [item["id"] for item in listed.json()["data"]] == [message_id]

    # Cancelling is addressed by message id: the scheduled message names its
    # own container, so no container segment appears.
    cancelled = await inprocess_client.delete(
        f"/messages/{message_id}/scheduled",
        headers=_auth(owner_tokens["access_token"]),
    )
    assert cancelled.status_code == 204, cancelled.text

    empty = await inprocess_client.get(
        f"/messages/{container_type}/{container_id}/scheduled",
        headers=_auth(owner_tokens["access_token"]),
    )
    assert empty.json()["data"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("container_type", ["conversation", "channel"])
async def test_clear_for_one_viewer_leaves_the_other_alone(
    inprocess_client, container_type
):
    owner, owner_tokens = await _create_verified_user_and_tokens(
        f"unified-clear-{container_type}@test.com"
    )
    member, member_tokens = await _create_verified_user_and_tokens(
        f"unified-clear-peer-{container_type}@test.com"
    )
    await _grant_chat_permission(str(owner["_id"]), str(member["_id"]))

    if container_type == "conversation":
        container_id = await _group(inprocess_client, owner_tokens, str(member["_id"]))
    else:
        container_id = await _channel(
            inprocess_client, owner_tokens, slug=f"clear-{container_type}"
        )

    await _send_text(
        inprocess_client, container_type, container_id, owner_tokens, "keep me"
    )

    cleared = await inprocess_client.delete(
        f"/messages/{container_type}/{container_id}",
        headers=_auth(member_tokens["access_token"]),
    )
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["data"]["cleared_count"] == 1

    # Hidden for the viewer who cleared...
    mine = await inprocess_client.get(
        f"/messages/{container_type}/{container_id}",
        headers=_auth(member_tokens["access_token"]),
    )
    assert mine.json()["data"] == []

    # ...and untouched for everyone else.
    theirs = await inprocess_client.get(
        f"/messages/{container_type}/{container_id}",
        headers=_auth(owner_tokens["access_token"]),
    )
    assert len(theirs.json()["data"]) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("container_type", ["conversation", "channel"])
async def test_clear_for_everyone_needs_the_containers_own_manage_right(
    inprocess_client, container_type
):
    owner, owner_tokens = await _create_verified_user_and_tokens(
        f"unified-clearall-{container_type}@test.com"
    )
    member, member_tokens = await _create_verified_user_and_tokens(
        f"unified-clearall-peer-{container_type}@test.com"
    )
    await _grant_chat_permission(str(owner["_id"]), str(member["_id"]))

    if container_type == "conversation":
        container_id = await _group(inprocess_client, owner_tokens, str(member["_id"]))
    else:
        container_id = await _channel(
            inprocess_client, owner_tokens, slug=f"clearall-{container_type}"
        )

    await _send_text(
        inprocess_client, container_type, container_id, owner_tokens, "for everyone"
    )

    refused = await inprocess_client.delete(
        f"/messages/{container_type}/{container_id}/all",
        headers=_auth(member_tokens["access_token"]),
    )
    assert refused.status_code == 403, refused.text

    cleared = await inprocess_client.delete(
        f"/messages/{container_type}/{container_id}/all",
        headers=_auth(owner_tokens["access_token"]),
    )
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["data"]["cleared_count"] == 1

    gone = await inprocess_client.get(
        f"/messages/{container_type}/{container_id}",
        headers=_auth(owner_tokens["access_token"]),
    )
    assert gone.json()["data"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("container_type", ["conversation", "channel"])
async def test_pinned_list_is_served_for_both_containers(
    inprocess_client, container_type
):
    owner, owner_tokens = await _create_verified_user_and_tokens(
        f"unified-pin-{container_type}@test.com"
    )
    member, _member_tokens = await _create_verified_user_and_tokens(
        f"unified-pin-peer-{container_type}@test.com"
    )
    await _grant_chat_permission(str(owner["_id"]), str(member["_id"]))

    if container_type == "conversation":
        container_id = await _group(inprocess_client, owner_tokens, str(member["_id"]))
    else:
        container_id = await _channel(
            inprocess_client, owner_tokens, slug=f"pin-{container_type}"
        )

    message = await _send_text(
        inprocess_client, container_type, container_id, owner_tokens, "pin me"
    )

    pinned = await inprocess_client.post(
        f"/messages/{message['id']}/pin",
        headers=_auth(owner_tokens["access_token"]),
    )
    assert pinned.status_code == 200, pinned.text
    assert pinned.json()["data"]["pinned_message_ids"] == [message["id"]]

    listed = await inprocess_client.get(
        f"/messages/{container_type}/{container_id}/pinned",
        headers=_auth(owner_tokens["access_token"]),
    )
    assert listed.status_code == 200, listed.text
    assert [item["id"] for item in listed.json()["data"]] == [message["id"]]


@pytest.mark.asyncio
async def test_channel_pinned_list_follows_the_read_policy(inprocess_client):
    owner, owner_tokens = await _create_verified_user_and_tokens(
        "unified-pin-policy-owner@test.com"
    )
    _stranger, stranger_tokens = await _create_verified_user_and_tokens(
        "unified-pin-policy-stranger@test.com"
    )

    public_id = await _channel(inprocess_client, owner_tokens, slug="pin-policy-public")
    private_id = await _channel(
        inprocess_client,
        owner_tokens,
        slug="pin-policy-private",
        visibility="members",
    )

    message = await _send_text(
        inprocess_client, "channel", public_id, owner_tokens, "public pin"
    )
    pinned = await inprocess_client.post(
        f"/messages/{message['id']}/pin",
        headers=_auth(owner_tokens["access_token"]),
    )
    assert pinned.status_code == 200, pinned.text

    # A public channel answers to a non-member: the read policy governs, not
    # membership.
    visible = await inprocess_client.get(
        f"/messages/channel/{public_id}/pinned",
        headers=_auth(stranger_tokens["access_token"]),
    )
    assert visible.status_code == 200, visible.text
    assert [item["id"] for item in visible.json()["data"]] == [message["id"]]

    hidden = await inprocess_client.get(
        f"/messages/channel/{private_id}/pinned",
        headers=_auth(stranger_tokens["access_token"]),
    )
    assert hidden.status_code == 403, hidden.text


@pytest.mark.asyncio
async def test_item_routes_win_over_the_container_family(inprocess_client):
    """The two families share a prefix and a segment count.

    `/messages/{message_id}/pin` and `/messages/{container_type}/{container_id}`
    are both two segments under `/messages`, so whichever router is registered
    first claims the request. If the container family ever moves ahead of the
    item family in `app/routes.py`, an unpin resolves to the container route and
    fails validation on a message id that is not a container type.
    """
    owner, owner_tokens = await _create_verified_user_and_tokens(
        "unified-order-owner@test.com"
    )
    member, _member_tokens = await _create_verified_user_and_tokens(
        "unified-order-peer@test.com"
    )
    await _grant_chat_permission(str(owner["_id"]), str(member["_id"]))
    conversation_id = await _group(inprocess_client, owner_tokens, str(member["_id"]))

    message = await _send_text(
        inprocess_client, "conversation", conversation_id, owner_tokens, "ordering"
    )
    await inprocess_client.post(
        f"/messages/{message['id']}/pin",
        headers=_auth(owner_tokens["access_token"]),
    )

    unpinned = await inprocess_client.delete(
        f"/messages/{message['id']}/pin",
        headers=_auth(owner_tokens["access_token"]),
    )
    assert unpinned.status_code == 200, unpinned.text
    assert unpinned.json()["data"]["pinned_message_ids"] == []

    scheduled_for = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    scheduled = await inprocess_client.post(
        f"/messages/conversation/{conversation_id}/schedule",
        json={"text": "later", "scheduled_for": scheduled_for},
        headers=_auth(owner_tokens["access_token"]),
    )
    assert scheduled.status_code == 201, scheduled.text

    cancelled = await inprocess_client.delete(
        f"/messages/{scheduled.json()['data']['id']}/scheduled",
        headers=_auth(owner_tokens["access_token"]),
    )
    assert cancelled.status_code == 204, cancelled.text
