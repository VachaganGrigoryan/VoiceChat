from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.db.mongo import get_db
from app.tests.integration.test_realtime_socket import (
    _create_verified_user_and_tokens,
    _grant_chat_permission,
)


def _auth(access_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {access_token}"}


async def _make_group(client, owner: dict, owner_tokens: dict, title: str = "Room") -> str:
    """Create a group to invite into.

    These scenarios exercise invites/join-requests, not channel behavior; channels are no
    longer a conversation type. The seed peer keeps the group valid and is distinct from
    every joiner/outsider the tests redeem with.
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


@pytest.mark.asyncio
async def test_create_and_redeem_invite_joins_directly(inprocess_client):
    owner, owner_tokens = await _create_verified_user_and_tokens("inv-o1@test.com")
    joiner, joiner_tokens = await _create_verified_user_and_tokens("inv-j1@test.com")
    conversation_id = await _make_group(inprocess_client, owner, owner_tokens)

    invite = await inprocess_client.post(
        f"/conversations/{conversation_id}/invites",
        json={},
        headers=_auth(owner_tokens["access_token"]),
    )
    assert invite.status_code == 201, invite.text
    code = invite.json()["data"]["code"]
    assert invite.json()["data"]["uses"] == 0

    redeem = await inprocess_client.post(
        f"/conversations/invites/{code}/redeem",
        headers=_auth(joiner_tokens["access_token"]),
    )
    assert redeem.status_code == 200, redeem.text
    data = redeem.json()["data"]
    assert data["status"] == "joined"
    assert data["membership"]["kind"] == "membership"
    assert data["membership"]["status"] == "active"
    assert data["membership"]["target_type"] == "conversation"
    assert data["membership"]["target_id"] == conversation_id
    assert data["conversation"]["id"] == conversation_id
    assert str(joiner["_id"]) in data["conversation"]["participant_ids"]
    # owner + seed peer + joiner
    assert data["conversation"]["member_count"] == 3

    # Joiner now sees the conversation in their inbox.
    inbox = await inprocess_client.get(
        "/conversations", headers=_auth(joiner_tokens["access_token"])
    )
    assert any(row["id"] == conversation_id for row in inbox.json()["data"])


@pytest.mark.asyncio
async def test_invite_requires_approval_creates_pending_membership(inprocess_client):
    owner, owner_tokens = await _create_verified_user_and_tokens("inv-o2@test.com")
    joiner, joiner_tokens = await _create_verified_user_and_tokens("inv-j2@test.com")
    conversation_id = await _make_group(inprocess_client, owner, owner_tokens)

    invite = await inprocess_client.post(
        f"/conversations/{conversation_id}/invites",
        json={"approval_required": True},
        headers=_auth(owner_tokens["access_token"]),
    )
    code = invite.json()["data"]["code"]

    redeem = await inprocess_client.post(
        f"/conversations/invites/{code}/redeem",
        headers=_auth(joiner_tokens["access_token"]),
    )
    assert redeem.status_code == 200, redeem.text
    data = redeem.json()["data"]
    assert data["status"] == "pending"
    assert data["conversation"] is None
    assert data["membership"]["kind"] == "membership"
    assert data["membership"]["status"] == "pending"
    assert data["membership"]["target_type"] == "conversation"
    assert data["membership"]["target_id"] == conversation_id
    request_id = data["membership"]["id"]
    assert "join_requests" not in await get_db().list_collection_names()

    # Owner sees the pending request.
    listing = await inprocess_client.get(
        f"/conversations/{conversation_id}/join-requests",
        headers=_auth(owner_tokens["access_token"]),
    )
    assert listing.status_code == 200, listing.text
    assert [r["id"] for r in listing.json()["data"]] == [request_id]

    # Approve adds the participant.
    approve = await inprocess_client.post(
        f"/conversations/{conversation_id}/join-requests/{request_id}/approve",
        headers=_auth(owner_tokens["access_token"]),
    )
    assert approve.status_code == 200, approve.text
    assert approve.json()["data"]["status"] == "approved"

    members = await inprocess_client.get(
        "/conversations", headers=_auth(joiner_tokens["access_token"])
    )
    assert any(row["id"] == conversation_id for row in members.json()["data"])


@pytest.mark.asyncio
async def test_reject_join_request(inprocess_client):
    owner, owner_tokens = await _create_verified_user_and_tokens("inv-o3@test.com")
    joiner, joiner_tokens = await _create_verified_user_and_tokens("inv-j3@test.com")
    conversation_id = await _make_group(inprocess_client, owner, owner_tokens)

    invite = await inprocess_client.post(
        f"/conversations/{conversation_id}/invites",
        json={"approval_required": True},
        headers=_auth(owner_tokens["access_token"]),
    )
    code = invite.json()["data"]["code"]
    redeem = await inprocess_client.post(
        f"/conversations/invites/{code}/redeem",
        headers=_auth(joiner_tokens["access_token"]),
    )
    request_id = redeem.json()["data"]["membership"]["id"]

    reject = await inprocess_client.post(
        f"/conversations/{conversation_id}/join-requests/{request_id}/reject",
        headers=_auth(owner_tokens["access_token"]),
    )
    assert reject.status_code == 200, reject.text
    assert reject.json()["data"]["status"] == "rejected"

    inbox = await inprocess_client.get(
        "/conversations", headers=_auth(joiner_tokens["access_token"])
    )
    assert not any(row["id"] == conversation_id for row in inbox.json()["data"])


@pytest.mark.asyncio
async def test_revoked_invite_is_rejected(inprocess_client):
    owner, owner_tokens = await _create_verified_user_and_tokens("inv-o4@test.com")
    joiner, joiner_tokens = await _create_verified_user_and_tokens("inv-j4@test.com")
    conversation_id = await _make_group(inprocess_client, owner, owner_tokens)

    invite = await inprocess_client.post(
        f"/conversations/{conversation_id}/invites",
        json={},
        headers=_auth(owner_tokens["access_token"]),
    )
    code = invite.json()["data"]["code"]
    invite_id = invite.json()["data"]["id"]

    revoke = await inprocess_client.delete(
        f"/conversations/{conversation_id}/invites/{invite_id}",
        headers=_auth(owner_tokens["access_token"]),
    )
    assert revoke.status_code == 204, revoke.text

    redeem = await inprocess_client.post(
        f"/conversations/invites/{code}/redeem",
        headers=_auth(joiner_tokens["access_token"]),
    )
    assert redeem.status_code == 410, redeem.text
    assert redeem.json()["error"]["code"] == "INVITE_UNAVAILABLE"


@pytest.mark.asyncio
async def test_expired_invite_is_rejected(inprocess_client):
    owner, owner_tokens = await _create_verified_user_and_tokens("inv-o5@test.com")
    joiner, joiner_tokens = await _create_verified_user_and_tokens("inv-j5@test.com")
    conversation_id = await _make_group(inprocess_client, owner, owner_tokens)

    past = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    invite = await inprocess_client.post(
        f"/conversations/{conversation_id}/invites",
        json={"expires_at": past},
        headers=_auth(owner_tokens["access_token"]),
    )
    code = invite.json()["data"]["code"]

    redeem = await inprocess_client.post(
        f"/conversations/invites/{code}/redeem",
        headers=_auth(joiner_tokens["access_token"]),
    )
    assert redeem.status_code == 410, redeem.text


@pytest.mark.asyncio
async def test_max_uses_exhausted(inprocess_client):
    owner, owner_tokens = await _create_verified_user_and_tokens("inv-o6@test.com")
    j1, j1_tokens = await _create_verified_user_and_tokens("inv-j6a@test.com")
    j2, j2_tokens = await _create_verified_user_and_tokens("inv-j6b@test.com")
    conversation_id = await _make_group(inprocess_client, owner, owner_tokens)

    invite = await inprocess_client.post(
        f"/conversations/{conversation_id}/invites",
        json={"max_uses": 1},
        headers=_auth(owner_tokens["access_token"]),
    )
    code = invite.json()["data"]["code"]

    first = await inprocess_client.post(
        f"/conversations/invites/{code}/redeem",
        headers=_auth(j1_tokens["access_token"]),
    )
    assert first.status_code == 200, first.text

    second = await inprocess_client.post(
        f"/conversations/invites/{code}/redeem",
        headers=_auth(j2_tokens["access_token"]),
    )
    assert second.status_code == 410, second.text


@pytest.mark.asyncio
async def test_non_manager_cannot_create_invite(inprocess_client):
    owner, owner_tokens = await _create_verified_user_and_tokens("inv-o7@test.com")
    outsider, outsider_tokens = await _create_verified_user_and_tokens("inv-x7@test.com")
    conversation_id = await _make_group(inprocess_client, owner, owner_tokens)

    resp = await inprocess_client.post(
        f"/conversations/{conversation_id}/invites",
        json={},
        headers=_auth(outsider_tokens["access_token"]),
    )
    assert resp.status_code in (403, 404), resp.text
