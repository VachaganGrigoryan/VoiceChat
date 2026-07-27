"""Space→child ownership, role inheritance, and access rules (§21, §53, §63–64)."""

from __future__ import annotations

import pytest

from app.tests.integration.test_realtime_socket import (
    _create_verified_user_and_tokens,
)


def _auth(access_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {access_token}"}


async def _make_space(client, token: str, slug: str) -> str:
    resp = await client.post(
        "/spaces",
        json={"name": slug.replace("-", " ").title(), "slug": slug},
        headers=_auth(token),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]["id"]


async def _add_member(
    client, space_id: str, owner_token: str, invitee_id: str, invitee_token: str
) -> str:
    """Invite a user and have them accept, returning the membership id.

    An invite lands as a `pending` membership; only acceptance makes it
    `active`, and only an active membership grants anything (§58).
    """
    resp = await client.post(
        f"/spaces/{space_id}/invite/{invitee_id}",
        headers=_auth(owner_token),
    )
    assert resp.status_code in (200, 201), resp.text
    relationship_id = resp.json()["data"]["id"]

    accepted = await client.post(
        f"/spaces/{space_id}/members/{relationship_id}/accept",
        headers=_auth(invitee_token),
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["data"]["status"] == "active"
    return relationship_id


@pytest.mark.asyncio
async def test_space_owns_channels_and_groups(inprocess_client):
    owner, tokens = await _create_verified_user_and_tokens("sgc-owner1@test.com")
    token = tokens["access_token"]
    space_id = await _make_space(inprocess_client, token, "sgc-space-one")

    resp = await inprocess_client.post(
        f"/spaces/{space_id}/channels",
        json={"name": "General", "slug": "sgc-general", "visibility": "members"},
        headers=_auth(token),
    )
    assert resp.status_code == 201, resp.text
    channel = resp.json()["data"]
    assert channel["owner"]["type"] == "space"
    assert str(channel["owner"]["id"]) == space_id
    assert str(channel["space_id"]) == space_id

    listed = await inprocess_client.get(
        f"/spaces/{space_id}/channels", headers=_auth(token)
    )
    assert listed.status_code == 200, listed.text
    assert [c["id"] for c in listed.json()["data"]] == [channel["id"]]

    member, member_tokens = await _create_verified_user_and_tokens("sgc-m1@test.com")
    await _add_member(
        inprocess_client, space_id, token, str(member["_id"]), member_tokens["access_token"]
    )

    resp = await inprocess_client.post(
        f"/spaces/{space_id}/groups",
        json={"title": "Team", "participant_ids": [str(member["_id"])]},
        headers=_auth(token),
    )
    assert resp.status_code == 201, resp.text
    group = resp.json()["data"]

    groups = await inprocess_client.get(
        f"/spaces/{space_id}/groups", headers=_auth(token)
    )
    assert groups.status_code == 200, groups.text
    assert [g["id"] for g in groups.json()["data"]] == [group["id"]]
    assert groups.json()["data"][0]["joined"] is True


@pytest.mark.asyncio
async def test_member_visibility_channel_readable_without_channel_membership(
    inprocess_client,
):
    """§63: space membership alone grants read on a `members` channel."""
    owner, owner_tokens = await _create_verified_user_and_tokens("sgc-owner2@test.com")
    owner_token = owner_tokens["access_token"]
    space_id = await _make_space(inprocess_client, owner_token, "sgc-space-two")

    created = await inprocess_client.post(
        f"/spaces/{space_id}/channels",
        json={"name": "Members Only", "slug": "sgc-members", "visibility": "members"},
        headers=_auth(owner_token),
    )
    assert created.status_code == 201, created.text
    channel_id = created.json()["data"]["id"]

    member, member_tokens = await _create_verified_user_and_tokens("sgc-m2@test.com")
    member_token = member_tokens["access_token"]
    await _add_member(
        inprocess_client, space_id, owner_token, str(member["_id"]), member_token
    )

    listed = await inprocess_client.get(
        f"/spaces/{space_id}/channels", headers=_auth(member_token)
    )
    assert listed.status_code == 200, listed.text
    entries = {c["id"]: c for c in listed.json()["data"]}
    assert channel_id in entries, "members-visibility channel must be visible"
    assert entries[channel_id]["joined"] is False, (
        "read access must not require an explicit channel membership"
    )

    read = await inprocess_client.get(
        f"/channels/{channel_id}/messages", headers=_auth(member_token)
    )
    assert read.status_code == 200, read.text


@pytest.mark.asyncio
async def test_private_space_channel_requires_channel_membership(inprocess_client):
    """§63: inheritance must not leak read into a private channel."""
    owner, owner_tokens = await _create_verified_user_and_tokens("sgc-owner3@test.com")
    owner_token = owner_tokens["access_token"]
    space_id = await _make_space(inprocess_client, owner_token, "sgc-space-three")

    created = await inprocess_client.post(
        f"/spaces/{space_id}/channels",
        json={"name": "Secret", "slug": "sgc-secret", "visibility": "private"},
        headers=_auth(owner_token),
    )
    assert created.status_code == 201, created.text
    channel_id = created.json()["data"]["id"]

    member, member_tokens = await _create_verified_user_and_tokens("sgc-m3@test.com")
    member_token = member_tokens["access_token"]
    await _add_member(
        inprocess_client, space_id, owner_token, str(member["_id"]), member_token
    )

    listed = await inprocess_client.get(
        f"/spaces/{space_id}/channels", headers=_auth(member_token)
    )
    assert listed.status_code == 200, listed.text
    assert channel_id not in {c["id"] for c in listed.json()["data"]}, (
        "a private space channel must stay hidden from a plain space member"
    )

    read = await inprocess_client.get(
        f"/channels/{channel_id}/messages", headers=_auth(member_token)
    )
    assert read.status_code == 403, read.text

    # Self-join must not be a back door into the private channel: creating your
    # own membership is exactly what would satisfy the private-read check.
    joined = await inprocess_client.post(
        f"/spaces/{space_id}/channels/{channel_id}/join",
        headers=_auth(member_token),
    )
    assert joined.status_code == 403, joined.text

    still_denied = await inprocess_client.get(
        f"/channels/{channel_id}/messages", headers=_auth(member_token)
    )
    assert still_denied.status_code == 403, still_denied.text

    owner_listed = await inprocess_client.get(
        f"/spaces/{space_id}/channels", headers=_auth(owner_token)
    )
    assert channel_id in {c["id"] for c in owner_listed.json()["data"]}, (
        "the effective owner still sees it"
    )


@pytest.mark.asyncio
async def test_members_visibility_channel_is_self_joinable(inprocess_client):
    """The mirror of the private case: `members` channels stay `open`."""
    owner, owner_tokens = await _create_verified_user_and_tokens("sgc-owner5@test.com")
    owner_token = owner_tokens["access_token"]
    space_id = await _make_space(inprocess_client, owner_token, "sgc-space-five")

    created = await inprocess_client.post(
        f"/spaces/{space_id}/channels",
        json={"name": "Open", "slug": "sgc-open", "visibility": "members"},
        headers=_auth(owner_token),
    )
    assert created.status_code == 201, created.text
    channel_id = created.json()["data"]["id"]

    member, member_tokens = await _create_verified_user_and_tokens("sgc-m5@test.com")
    member_token = member_tokens["access_token"]
    await _add_member(
        inprocess_client, space_id, owner_token, str(member["_id"]), member_token
    )

    joined = await inprocess_client.post(
        f"/spaces/{space_id}/channels/{channel_id}/join",
        headers=_auth(member_token),
    )
    assert joined.status_code == 204, joined.text

    listed = await inprocess_client.get(
        f"/spaces/{space_id}/channels", headers=_auth(member_token)
    )
    entry = next(c for c in listed.json()["data"] if c["id"] == channel_id)
    assert entry["joined"] is True


@pytest.mark.asyncio
async def test_invite_requires_member_invite_permission(inprocess_client):
    """`POST /spaces/{id}/invite/{uid}` is an authority, not a free action."""
    owner, owner_tokens = await _create_verified_user_and_tokens("sgc-owner6@test.com")
    owner_token = owner_tokens["access_token"]
    space_id = await _make_space(inprocess_client, owner_token, "sgc-space-six")

    outsider, outsider_tokens = await _create_verified_user_and_tokens(
        "sgc-out6@test.com"
    )
    victim, _ = await _create_verified_user_and_tokens("sgc-victim6@test.com")

    denied = await inprocess_client.post(
        f"/spaces/{space_id}/invite/{victim['_id']}",
        headers=_auth(outsider_tokens["access_token"]),
    )
    assert denied.status_code == 403, denied.text

    allowed = await inprocess_client.post(
        f"/spaces/{space_id}/invite/{victim['_id']}",
        headers=_auth(owner_token),
    )
    assert allowed.status_code == 201, allowed.text


@pytest.mark.asyncio
async def test_join_request_cannot_be_self_accepted(inprocess_client):
    """A join request needs `member.approve`, not the requester's own accept."""
    owner, owner_tokens = await _create_verified_user_and_tokens("sgc-owner7@test.com")
    owner_token = owner_tokens["access_token"]
    space_id = await _make_space(inprocess_client, owner_token, "sgc-space-seven")

    joiner, joiner_tokens = await _create_verified_user_and_tokens("sgc-join7@test.com")
    joiner_token = joiner_tokens["access_token"]

    requested = await inprocess_client.post(
        f"/spaces/{space_id}/join", headers=_auth(joiner_token)
    )
    assert requested.status_code == 201, requested.text
    relationship_id = requested.json()["data"]["id"]

    self_accept = await inprocess_client.post(
        f"/spaces/{space_id}/members/{relationship_id}/accept",
        headers=_auth(joiner_token),
    )
    assert self_accept.status_code == 403, self_accept.text

    members = await inprocess_client.get(
        f"/spaces/{space_id}/members", headers=_auth(owner_token)
    )
    assert str(joiner["_id"]) not in {m["user_id"] for m in members.json()["data"]}


@pytest.mark.asyncio
async def test_space_member_is_not_automatically_a_group_participant(inprocess_client):
    """§64: manage is inherited, participation is not."""
    owner, owner_tokens = await _create_verified_user_and_tokens("sgc-owner4@test.com")
    owner_token = owner_tokens["access_token"]
    space_id = await _make_space(inprocess_client, owner_token, "sgc-space-four")

    insider, insider_tokens = await _create_verified_user_and_tokens("sgc-in4@test.com")
    await _add_member(
        inprocess_client,
        space_id,
        owner_token,
        str(insider["_id"]),
        insider_tokens["access_token"],
    )

    outsider, outsider_tokens = await _create_verified_user_and_tokens(
        "sgc-out4@test.com"
    )
    outsider_token = outsider_tokens["access_token"]
    await _add_member(
        inprocess_client, space_id, owner_token, str(outsider["_id"]), outsider_token
    )

    created = await inprocess_client.post(
        f"/spaces/{space_id}/groups",
        json={"title": "Closed Team", "participant_ids": [str(insider["_id"])]},
        headers=_auth(owner_token),
    )
    assert created.status_code == 201, created.text
    group_id = created.json()["data"]["id"]

    listed = await inprocess_client.get(
        f"/spaces/{space_id}/groups", headers=_auth(outsider_token)
    )
    assert listed.status_code == 200, listed.text
    entry = next(g for g in listed.json()["data"] if g["id"] == group_id)
    assert entry["joined"] is False, (
        "a space member who was not added is not a group participant"
    )

    posted = await inprocess_client.post(
        f"/conversations/{group_id}/messages/text",
        json={"text": "am I in?"},
        headers=_auth(outsider_token),
    )
    assert posted.status_code in (403, 404), (
        f"space membership must not confer group participation, got {posted.status_code}"
    )
