from __future__ import annotations

import pytest

from app.tests.integration.test_realtime_socket import (
    _create_verified_user_and_tokens,
    _grant_chat_permission,
)


def _auth(access_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {access_token}"}


async def _channel(client, tokens, **overrides):
    body = {
        "name": "Announcements",
        "slug": overrides.pop("slug", "announcements"),
        "visibility": "public",
        "posting_policy": "owner",
        "comment_policy": "everyone",
    }
    body.update(overrides)
    resp = await client.post("/channels", json=body, headers=_auth(tokens["access_token"]))
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]


@pytest.mark.asyncio
async def test_capabilities_match_the_decision_for_the_owner(inprocess_client):
    owner, owner_tokens = await _create_verified_user_and_tokens("cap-owner@test.com")
    channel = await _channel(inprocess_client, owner_tokens, slug="cap-owner-ch")

    resp = await inprocess_client.get(
        f"/channels/{channel['id']}/capabilities",
        headers=_auth(owner_tokens["access_token"]),
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]

    assert "message.create" in data["allowed"]
    assert data["standing"]["is_owner"] is True
    assert data["resource"] == {"type": "channel", "id": channel["id"]}

    # The claim is testable: posting must actually succeed.
    posted = await inprocess_client.post(
        f"/channels/{channel['id']}/messages",
        json={"text": "hello"},
        headers=_auth(owner_tokens["access_token"]),
    )
    assert posted.status_code == 201, posted.text


@pytest.mark.asyncio
async def test_denied_capability_matches_a_real_403(inprocess_client):
    owner, owner_tokens = await _create_verified_user_and_tokens("cap-o2@test.com")
    other, other_tokens = await _create_verified_user_and_tokens("cap-x2@test.com")
    # posting_policy=owner, so a stranger may read but not post.
    channel = await _channel(inprocess_client, owner_tokens, slug="cap-owner-only")

    resp = await inprocess_client.get(
        f"/channels/{channel['id']}/capabilities",
        headers=_auth(other_tokens["access_token"]),
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]

    assert "message.create" in data["denied"]
    assert "message.read" in data["allowed"]
    assert data["standing"]["is_owner"] is False

    # The denial is real, not cosmetic.
    posted = await inprocess_client.post(
        f"/channels/{channel['id']}/messages",
        json={"text": "nope"},
        headers=_auth(other_tokens["access_token"]),
    )
    assert posted.status_code == 403, posted.text


@pytest.mark.asyncio
async def test_every_requested_action_appears_in_exactly_one_list(inprocess_client):
    owner, owner_tokens = await _create_verified_user_and_tokens("cap-o3@test.com")
    channel = await _channel(inprocess_client, owner_tokens, slug="cap-partition")

    requested = ["message.read", "message.create", "resource.manage", "message.pin"]
    resp = await inprocess_client.post(
        "/viewer/capabilities",
        json={
            "resources": [{"type": "channel", "id": channel["id"]}],
            "actions": requested,
        },
        headers=_auth(owner_tokens["access_token"]),
    )
    assert resp.status_code == 200, resp.text
    entry = resp.json()["data"]["capabilities"][0]

    # Explicit deny is the contract: absent must mean "not evaluated".
    assert set(entry["allowed"]) | set(entry["denied"]) == set(requested)
    assert not set(entry["allowed"]) & set(entry["denied"])
    assert "thread.reply" not in entry["allowed"]
    assert "thread.reply" not in entry["denied"]


@pytest.mark.asyncio
async def test_batch_and_single_endpoints_agree(inprocess_client):
    owner, owner_tokens = await _create_verified_user_and_tokens("cap-o4@test.com")
    channel = await _channel(inprocess_client, owner_tokens, slug="cap-agree")

    single = await inprocess_client.get(
        f"/channels/{channel['id']}/capabilities",
        headers=_auth(owner_tokens["access_token"]),
    )
    batch = await inprocess_client.post(
        "/viewer/capabilities",
        json={"resources": [{"type": "channel", "id": channel["id"]}]},
        headers=_auth(owner_tokens["access_token"]),
    )
    assert single.status_code == 200
    assert batch.status_code == 200
    assert sorted(single.json()["data"]["allowed"]) == sorted(
        batch.json()["data"]["capabilities"][0]["allowed"]
    )


@pytest.mark.asyncio
async def test_own_scoped_actions_are_evaluated_as_the_caller(inprocess_client):
    owner, owner_tokens = await _create_verified_user_and_tokens("cap-o5@test.com")
    channel = await _channel(inprocess_client, owner_tokens, slug="cap-own")

    resp = await inprocess_client.post(
        "/viewer/capabilities",
        json={
            "resources": [{"type": "channel", "id": channel["id"]}],
            "actions": ["message.edit.own", "message.delete.own"],
        },
        headers=_auth(owner_tokens["access_token"]),
    )
    assert resp.status_code == 200, resp.text
    allowed = resp.json()["data"]["capabilities"][0]["allowed"]
    # Granted means "on content you authored" — never unconditionally.
    assert "message.edit.own" in allowed


@pytest.mark.asyncio
async def test_batch_over_the_limit_is_rejected(inprocess_client):
    owner, owner_tokens = await _create_verified_user_and_tokens("cap-o6@test.com")
    channel = await _channel(inprocess_client, owner_tokens, slug="cap-limit")

    resp = await inprocess_client.post(
        "/viewer/capabilities",
        json={
            "resources": [{"type": "channel", "id": channel["id"]}] * 51,
        },
        headers=_auth(owner_tokens["access_token"]),
    )
    assert resp.status_code == 422, resp.text


@pytest.mark.asyncio
async def test_standing_distinguishes_follower_from_member(inprocess_client):
    owner, owner_tokens = await _create_verified_user_and_tokens("cap-o7@test.com")
    viewer, viewer_tokens = await _create_verified_user_and_tokens("cap-f7@test.com")
    channel = await _channel(
        inprocess_client, owner_tokens, slug="cap-standing", posting_policy="everyone"
    )

    followed = await inprocess_client.post(
        f"/channels/{channel['id']}/follow",
        headers=_auth(viewer_tokens["access_token"]),
    )
    assert followed.status_code == 201, followed.text

    resp = await inprocess_client.get(
        f"/channels/{channel['id']}/capabilities",
        headers=_auth(viewer_tokens["access_token"]),
    )
    standing = resp.json()["data"]["standing"]
    # A follower who never joined: permissions alone could not express this.
    assert standing["is_follower"] is True
    assert standing["membership_status"] != "active"
    assert standing["is_owner"] is False


@pytest.mark.asyncio
async def test_policy_echo_is_present_for_management_forms(inprocess_client):
    owner, owner_tokens = await _create_verified_user_and_tokens("cap-o8@test.com")
    channel = await _channel(inprocess_client, owner_tokens, slug="cap-policy")

    resp = await inprocess_client.get(
        f"/channels/{channel['id']}/capabilities",
        headers=_auth(owner_tokens["access_token"]),
    )
    policy = resp.json()["data"]["policy"]
    assert policy["visibility"] == "public"
    assert policy["posting_policy"] == "owner"
    assert policy["comment_policy"] == "everyone"


@pytest.mark.asyncio
async def test_conversation_capabilities_resolve(inprocess_client):
    owner, owner_tokens = await _create_verified_user_and_tokens("cap-c1@test.com")
    peer, peer_tokens = await _create_verified_user_and_tokens("cap-c2@test.com")
    await _grant_chat_permission(str(owner["_id"]), str(peer["_id"]))

    group = await inprocess_client.post(
        "/conversations/groups",
        json={"title": "Caps", "participant_ids": [str(peer["_id"])]},
        headers=_auth(owner_tokens["access_token"]),
    )
    conversation_id = group.json()["data"]["id"]

    resp = await inprocess_client.get(
        f"/conversations/{conversation_id}/capabilities",
        headers=_auth(peer_tokens["access_token"]),
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert "message.create" in data["allowed"]
    assert data["standing"]["membership_status"] == "active"


@pytest.mark.asyncio
async def test_embedded_viewer_block_agrees_with_the_full_result(inprocess_client):
    """The prefetch is only safe if it cannot contradict the contract."""
    owner, owner_tokens = await _create_verified_user_and_tokens("cap-o9@test.com")
    other, other_tokens = await _create_verified_user_and_tokens("cap-x9@test.com")
    channel = await _channel(inprocess_client, owner_tokens, slug="cap-embed")

    for tokens in (owner_tokens, other_tokens):
        detail = await inprocess_client.get(
            f"/channels/{channel['id']}", headers=_auth(tokens["access_token"])
        )
        assert detail.status_code == 200, detail.text
        viewer = detail.json()["data"]["viewer"]

        caps = await inprocess_client.get(
            f"/channels/{channel['id']}/capabilities",
            headers=_auth(tokens["access_token"]),
        )
        allowed = set(caps.json()["data"]["allowed"])

        assert viewer["can_post"] is ("message.create" in allowed)
        assert viewer["can_comment"] is ("thread.reply" in allowed)
        assert viewer["can_manage"] is ("resource.manage" in allowed)


@pytest.mark.asyncio
async def test_capabilities_require_authentication(inprocess_client):
    resp = await inprocess_client.post(
        "/viewer/capabilities",
        json={"resources": [{"type": "channel", "id": "000000000000000000000000"}]},
    )
    assert resp.status_code in (401, 403), resp.text
