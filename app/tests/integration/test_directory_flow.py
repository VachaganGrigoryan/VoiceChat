from __future__ import annotations

import pytest

from app.tests.integration.test_realtime_socket import (
    _create_verified_user_and_tokens,
    _grant_chat_permission,
)


def _auth(access_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {access_token}"}


async def _channel(client, tokens, slug: str, **overrides):
    body = {"name": slug.replace("-", " ").title(), "slug": slug}
    body.update(overrides)
    resp = await client.post("/channels", json=body, headers=_auth(tokens["access_token"]))
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]


async def _slugs(client, tokens, path="/directory/channels", **params):
    resp = await client.get(path, params=params, headers=_auth(tokens["access_token"]))
    assert resp.status_code == 200, resp.text
    return resp.json(), [item.get("slug") for item in resp.json()["data"]]


@pytest.mark.asyncio
async def test_public_channel_is_listed(inprocess_client):
    owner, owner_tokens = await _create_verified_user_and_tokens("dir-o1@test.com")
    seeker, seeker_tokens = await _create_verified_user_and_tokens("dir-s1@test.com")
    await _channel(inprocess_client, owner_tokens, "dir-public", visibility="public")

    _, slugs = await _slugs(inprocess_client, seeker_tokens, q="dir-public")
    assert "dir-public" in slugs


@pytest.mark.asyncio
async def test_members_visibility_channel_is_listable_but_not_readable(inprocess_client):
    """Findable before joinable — otherwise `join_policy=approval` is unusable."""
    owner, owner_tokens = await _create_verified_user_and_tokens("dir-o2@test.com")
    seeker, seeker_tokens = await _create_verified_user_and_tokens("dir-s2@test.com")
    channel = await _channel(
        inprocess_client,
        owner_tokens,
        "dir-members",
        visibility="members",
        join_policy="approval",
    )

    _, slugs = await _slugs(inprocess_client, seeker_tokens, q="dir-members")
    assert "dir-members" in slugs

    content = await inprocess_client.get(
        f"/messages/channel/{channel['id']}",
        headers=_auth(seeker_tokens["access_token"]),
    )
    assert content.status_code == 403, content.text


@pytest.mark.asyncio
async def test_private_channel_is_not_listed(inprocess_client):
    owner, owner_tokens = await _create_verified_user_and_tokens("dir-o3@test.com")
    seeker, seeker_tokens = await _create_verified_user_and_tokens("dir-s3@test.com")
    await _channel(inprocess_client, owner_tokens, "dir-private", visibility="private")

    _, slugs = await _slugs(inprocess_client, seeker_tokens, q="dir-private")
    assert "dir-private" not in slugs


@pytest.mark.asyncio
async def test_closed_join_policy_is_suppressed(inprocess_client):
    owner, owner_tokens = await _create_verified_user_and_tokens("dir-o4@test.com")
    seeker, seeker_tokens = await _create_verified_user_and_tokens("dir-s4@test.com")
    await _channel(
        inprocess_client, owner_tokens, "dir-closed", join_policy="closed"
    )

    _, slugs = await _slugs(inprocess_client, seeker_tokens, q="dir-closed")
    assert "dir-closed" not in slugs


@pytest.mark.asyncio
async def test_profile_channels_do_not_flood_the_directory(inprocess_client):
    owner, owner_tokens = await _create_verified_user_and_tokens("dir-o5@test.com")
    seeker, seeker_tokens = await _create_verified_user_and_tokens("dir-s5@test.com")

    body, _ = await _slugs(inprocess_client, seeker_tokens)
    assert all(item["kind"] != "profile" for item in body["data"])

    # ...but they are reachable when explicitly asked for.
    resp = await inprocess_client.get(
        "/directory/channels",
        params={"kind": "profile", "owner_type": "user"},
        headers=_auth(seeker_tokens["access_token"]),
    )
    assert resp.status_code == 200, resp.text


@pytest.mark.asyncio
async def test_blocked_owner_channels_are_hidden_both_ways(inprocess_client):
    owner, owner_tokens = await _create_verified_user_and_tokens("dir-o6@test.com")
    seeker, seeker_tokens = await _create_verified_user_and_tokens("dir-s6@test.com")
    await _channel(inprocess_client, owner_tokens, "dir-blocked")

    blocked = await inprocess_client.post(
        f"/blocks/{owner['_id']}", headers=_auth(seeker_tokens["access_token"])
    )
    assert blocked.status_code == 201, blocked.text

    _, slugs = await _slugs(inprocess_client, seeker_tokens, q="dir-blocked")
    assert "dir-blocked" not in slugs

    # And the other direction: the blocked user does not see the blocker's.
    await _channel(inprocess_client, seeker_tokens, "dir-blocker-owned")
    _, owner_view = await _slugs(inprocess_client, owner_tokens, q="dir-blocker-owned")
    assert "dir-blocker-owned" not in owner_view


@pytest.mark.asyncio
async def test_channel_paging_is_stable_across_an_insertion(inprocess_client):
    owner, owner_tokens = await _create_verified_user_and_tokens("dir-o7@test.com")
    seeker, seeker_tokens = await _create_verified_user_and_tokens("dir-s7@test.com")
    for index in range(4):
        await _channel(inprocess_client, owner_tokens, f"dir-page-{index}")

    first = await inprocess_client.get(
        "/directory/channels",
        params={"q": "dir-page", "limit": 2},
        headers=_auth(seeker_tokens["access_token"]),
    )
    assert first.status_code == 200, first.text
    page_one = [item["slug"] for item in first.json()["data"]]
    cursor = first.json()["meta"]["next_cursor"]
    assert cursor

    # A new match arrives between the two page requests.
    await _channel(inprocess_client, owner_tokens, "dir-page-new")

    second = await inprocess_client.get(
        "/directory/channels",
        params={"q": "dir-page", "limit": 2, "cursor": cursor},
        headers=_auth(seeker_tokens["access_token"]),
    )
    page_two = [item["slug"] for item in second.json()["data"]]
    assert not set(page_one) & set(page_two), (page_one, page_two)


@pytest.mark.asyncio
async def test_directory_total_is_null_rather_than_fabricated(inprocess_client):
    _, tokens = await _create_verified_user_and_tokens("dir-s8@test.com")
    body, _ = await _slugs(inprocess_client, tokens)
    assert body["meta"]["total"] is None


@pytest.mark.asyncio
async def test_public_space_is_listed_and_private_is_not(inprocess_client):
    owner, owner_tokens = await _create_verified_user_and_tokens("dir-sp1@test.com")
    seeker, seeker_tokens = await _create_verified_user_and_tokens("dir-sp2@test.com")

    for slug, visibility in (("dir-open-space", "public"), ("dir-shut-space", "private")):
        resp = await inprocess_client.post(
            "/spaces",
            json={"name": slug, "slug": slug, "visibility": visibility},
            headers=_auth(owner_tokens["access_token"]),
        )
        assert resp.status_code == 201, resp.text

    resp = await inprocess_client.get(
        "/directory/spaces", headers=_auth(seeker_tokens["access_token"])
    )
    assert resp.status_code == 200, resp.text
    slugs = [item["slug"] for item in resp.json()["data"]]
    assert "dir-open-space" in slugs
    assert "dir-shut-space" not in slugs


@pytest.mark.asyncio
async def test_default_space_sorts_first_and_is_flagged(inprocess_client):
    _, tokens = await _create_verified_user_and_tokens("dir-sp3@test.com")
    # `/spaces/me` is what materializes the default space.
    await inprocess_client.get("/spaces/me", headers=_auth(tokens["access_token"]))

    resp = await inprocess_client.get(
        "/directory/spaces", headers=_auth(tokens["access_token"])
    )
    spaces = resp.json()["data"]
    assert spaces
    assert spaces[0]["slug"] == "vogi"
    assert spaces[0]["is_default"] is True


@pytest.mark.asyncio
async def test_spaceless_group_is_never_listed(inprocess_client):
    """The invariant most likely to be got wrong."""
    owner, owner_tokens = await _create_verified_user_and_tokens("dir-g1@test.com")
    peer, peer_tokens = await _create_verified_user_and_tokens("dir-g2@test.com")
    await _grant_chat_permission(str(owner["_id"]), str(peer["_id"]))

    created = await inprocess_client.post(
        "/conversations/groups",
        json={"title": "Ad hoc", "participant_ids": [str(peer["_id"])]},
        headers=_auth(owner_tokens["access_token"]),
    )
    assert created.status_code == 201, created.text

    resp = await inprocess_client.get(
        "/directory/groups",
        params={"q": "Ad hoc"},
        headers=_auth(owner_tokens["access_token"]),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"] == []


@pytest.mark.asyncio
async def test_people_alias_matches_discovery(inprocess_client):
    _, seeker_tokens = await _create_verified_user_and_tokens("dir-p1@test.com")
    target, target_tokens = await _create_verified_user_and_tokens("dir-p2@test.com")

    username = (
        await inprocess_client.get(
            "/users/me", headers=_auth(target_tokens["access_token"])
        )
    ).json()["data"]["username"]

    via_directory = await inprocess_client.get(
        "/directory/people",
        params={"q": username},
        headers=_auth(seeker_tokens["access_token"]),
    )
    via_discovery = await inprocess_client.get(
        "/discovery/users/search",
        params={"q": username},
        headers=_auth(seeker_tokens["access_token"]),
    )
    assert via_directory.status_code == 200, via_directory.text
    assert [user["id"] for user in via_directory.json()["data"]] == [
        user["id"] for user in via_discovery.json()["data"]
    ]


@pytest.mark.asyncio
async def test_omni_previews_types_and_offers_no_cursor(inprocess_client):
    owner, owner_tokens = await _create_verified_user_and_tokens("dir-om@test.com")
    await _channel(inprocess_client, owner_tokens, "dir-omni-ch")

    resp = await inprocess_client.get(
        "/directory",
        params={"q": "dir-omni"},
        headers=_auth(owner_tokens["access_token"]),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert set(body["data"]) == {"spaces", "channels", "groups", "people"}
    assert "meta" not in body or body.get("meta") is None
    assert any(item["slug"] == "dir-omni-ch" for item in body["data"]["channels"])


@pytest.mark.asyncio
async def test_directory_requires_authentication(inprocess_client):
    resp = await inprocess_client.get("/directory/channels")
    assert resp.status_code in (401, 403), resp.text
