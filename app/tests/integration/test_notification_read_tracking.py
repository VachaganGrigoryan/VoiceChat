from __future__ import annotations

import pytest

from app.tests.integration.test_realtime_socket import (
    _create_verified_user_and_tokens,
    _grant_chat_permission,
)


def _auth(access_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {access_token}"}


async def _notified_user(client, sender_email: str, recipient_email: str):
    """Produce a real notification by sending the recipient a message."""
    sender, sender_tokens = await _create_verified_user_and_tokens(sender_email)
    recipient, recipient_tokens = await _create_verified_user_and_tokens(recipient_email)
    await _grant_chat_permission(str(sender["_id"]), str(recipient["_id"]))

    dm = await client.post(
        "/conversations",
        json={"peer_user_id": str(recipient["_id"])},
        headers=_auth(sender_tokens["access_token"]),
    )
    assert dm.status_code == 200, dm.text
    conversation_id = dm.json()["data"]["id"]

    sent = await client.post(
        f"/conversations/{conversation_id}/messages/text",
        json={"text": "ping"},
        headers=_auth(sender_tokens["access_token"]),
    )
    assert sent.status_code == 201, sent.text
    return recipient, recipient_tokens


@pytest.mark.asyncio
async def test_unread_count_reflects_a_new_notification(inprocess_client):
    _, tokens = await _notified_user(
        inprocess_client, "nrt-s1@test.com", "nrt-r1@test.com"
    )

    resp = await inprocess_client.get(
        "/notifications/unread-count", headers=_auth(tokens["access_token"])
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["count"] >= 1


@pytest.mark.asyncio
async def test_marking_one_read_sets_read_at_and_lowers_the_count(inprocess_client):
    _, tokens = await _notified_user(
        inprocess_client, "nrt-s2@test.com", "nrt-r2@test.com"
    )
    listing = await inprocess_client.get(
        "/notifications", headers=_auth(tokens["access_token"])
    )
    notification = listing.json()["data"][0]
    assert notification["read_at"] is None

    before = (
        await inprocess_client.get(
            "/notifications/unread-count", headers=_auth(tokens["access_token"])
        )
    ).json()["data"]["count"]

    marked = await inprocess_client.post(
        f"/notifications/{notification['id']}/read",
        headers=_auth(tokens["access_token"]),
    )
    assert marked.status_code == 200, marked.text
    assert marked.json()["data"]["read_at"] is not None

    after = (
        await inprocess_client.get(
            "/notifications/unread-count", headers=_auth(tokens["access_token"])
        )
    ).json()["data"]["count"]
    assert after == before - 1


@pytest.mark.asyncio
async def test_marking_read_twice_is_idempotent(inprocess_client):
    _, tokens = await _notified_user(
        inprocess_client, "nrt-s3@test.com", "nrt-r3@test.com"
    )
    listing = await inprocess_client.get(
        "/notifications", headers=_auth(tokens["access_token"])
    )
    notification_id = listing.json()["data"][0]["id"]

    first = await inprocess_client.post(
        f"/notifications/{notification_id}/read", headers=_auth(tokens["access_token"])
    )
    second = await inprocess_client.post(
        f"/notifications/{notification_id}/read", headers=_auth(tokens["access_token"])
    )
    assert first.status_code == 200
    assert second.status_code == 200
    # The original timestamp survives, so "when did I see this" stays answerable.
    assert first.json()["data"]["read_at"] == second.json()["data"]["read_at"]


@pytest.mark.asyncio
async def test_mark_all_read_zeroes_the_count(inprocess_client):
    _, tokens = await _notified_user(
        inprocess_client, "nrt-s4@test.com", "nrt-r4@test.com"
    )

    resp = await inprocess_client.post(
        "/notifications/read-all", headers=_auth(tokens["access_token"])
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["updated"] >= 1

    count = await inprocess_client.get(
        "/notifications/unread-count", headers=_auth(tokens["access_token"])
    )
    assert count.json()["data"]["count"] == 0


@pytest.mark.asyncio
async def test_cannot_mark_another_users_notification(inprocess_client):
    _, tokens = await _notified_user(
        inprocess_client, "nrt-s5@test.com", "nrt-r5@test.com"
    )
    _, stranger_tokens = await _create_verified_user_and_tokens("nrt-x5@test.com")

    listing = await inprocess_client.get(
        "/notifications", headers=_auth(tokens["access_token"])
    )
    notification_id = listing.json()["data"][0]["id"]

    resp = await inprocess_client.post(
        f"/notifications/{notification_id}/read",
        headers=_auth(stranger_tokens["access_token"]),
    )
    assert resp.status_code == 404, resp.text
