"""Hard deletion against a real database.

The unit suite can only assert which collections the cascade *targets*. These
tests assert what actually matters: after a delete, nothing in the database
still names the deleted resource.
"""

from __future__ import annotations

import pytest

from app.db.models.embedded import OwnerRef
from app.db.models import (
    ChannelDocument,
    ConversationDocument,
    InviteLinkDocument,
    MessageDocument,
    RelationshipDocument,
    RoleDocument,
    SpaceDocument,
)
from app.tests.integration.test_realtime_socket import (
    _create_verified_user_and_tokens,
)


def _auth(access_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {access_token}"}


async def _orphans_naming(resource_type: str, resource_id: str) -> dict[str, int]:
    """Every row that still names the resource, per collection."""
    counts = {
        "relationships": await RelationshipDocument.find(
            RelationshipDocument.target_type == resource_type,
            RelationshipDocument.target_id == resource_id,
        ).count(),
        "roles": await RoleDocument.find(
            RoleDocument.scope_type == resource_type,
            RoleDocument.scope_id == resource_id,
        ).count(),
        "invite_links": await InviteLinkDocument.find(
            InviteLinkDocument.target_type == resource_type,
            InviteLinkDocument.target_id == resource_id,
        ).count(),
    }
    if resource_type in ("conversation", "channel"):
        counts["messages"] = await MessageDocument.find(
            MessageDocument.container_type == resource_type,
            MessageDocument.container_id == resource_id,
        ).count()
    return {name: count for name, count in counts.items() if count}


@pytest.mark.asyncio
async def test_deleting_a_channel_leaves_nothing_naming_it(inprocess_client):
    owner, owner_tokens = await _create_verified_user_and_tokens("del-chan@test.com")

    created = await inprocess_client.post(
        "/channels",
        json={"name": "Doomed", "slug": "doomed"},
        headers=_auth(owner_tokens["access_token"]),
    )
    assert created.status_code == 201, created.text
    channel_id = created.json()["data"]["id"]

    posted = await inprocess_client.post(
        f"/messages/channel/{channel_id}/text",
        json={"text": "this should not survive"},
        headers=_auth(owner_tokens["access_token"]),
    )
    assert posted.status_code == 201, posted.text
    assert await _orphans_naming("channel", channel_id)  # precondition

    deleted = await inprocess_client.delete(
        f"/channels/{channel_id}", headers=_auth(owner_tokens["access_token"])
    )
    assert deleted.status_code == 204, deleted.text

    assert await ChannelDocument.get(channel_id) is None
    assert await _orphans_naming("channel", channel_id) == {}


@pytest.mark.asyncio
async def test_deleting_a_group_leaves_nothing_naming_it(inprocess_client):
    owner, owner_tokens = await _create_verified_user_and_tokens("del-group@test.com")
    owner_id = str(owner["_id"])

    # Created directly: group creation requires an accepted connection between
    # members, which is eligibility, not the deletion behaviour under test.
    group = await ConversationDocument(
        type="group",
        participant_ids=[owner_id],
        created_by=owner_id,
        owner=OwnerRef(type="user", id=owner_id),
        title="Doomed Group",
    ).insert()
    group_id = group.str_id

    await inprocess_client.post(
        f"/messages/conversation/{group_id}/text",
        json={"text": "this should not survive"},
        headers=_auth(owner_tokens["access_token"]),
    )

    deleted = await inprocess_client.delete(
        f"/conversations/groups/{group_id}", headers=_auth(owner_tokens["access_token"])
    )
    assert deleted.status_code == 204, deleted.text

    assert await ConversationDocument.get(group_id) is None
    assert await _orphans_naming("conversation", group_id) == {}


@pytest.mark.asyncio
async def test_deleting_a_space_takes_its_children_with_it(inprocess_client):
    owner, owner_tokens = await _create_verified_user_and_tokens("del-space@test.com")
    headers = _auth(owner_tokens["access_token"])

    created = await inprocess_client.post(
        "/spaces",
        json={"name": "Doomed Space", "slug": "doomed-space"},
        headers=headers,
    )
    assert created.status_code in (200, 201), created.text
    space_id = created.json()["data"]["id"]

    child_channel = await inprocess_client.post(
        f"/spaces/{space_id}/channels",
        json={"name": "Child", "slug": "child"},
        headers=headers,
    )
    assert child_channel.status_code in (200, 201), child_channel.text
    channel_id = child_channel.json()["data"]["id"]

    deleted = await inprocess_client.delete(f"/spaces/{space_id}", headers=headers)
    assert deleted.status_code == 204, deleted.text

    assert await SpaceDocument.get(space_id) is None
    # A child left behind would resolve no owner and could never be managed again.
    assert await ChannelDocument.get(channel_id) is None
    assert await _orphans_naming("space", space_id) == {}
    assert await _orphans_naming("channel", channel_id) == {}


@pytest.mark.asyncio
async def test_the_default_space_cannot_be_deleted(inprocess_client):
    _owner, owner_tokens = await _create_verified_user_and_tokens(
        "del-default@test.com"
    )
    headers = _auth(owner_tokens["access_token"])

    listed = await inprocess_client.get("/spaces/me", headers=headers)
    assert listed.status_code == 200, listed.text
    default = next(
        (space for space in listed.json()["data"] if space.get("is_default")), None
    )
    if default is None:
        pytest.skip("no default space provisioned for this user")

    refused = await inprocess_client.delete(f"/spaces/{default['id']}", headers=headers)
    assert refused.status_code == 409, refused.text
    assert await SpaceDocument.get(default["id"]) is not None


@pytest.mark.asyncio
async def test_a_stranger_cannot_delete_a_channel(inprocess_client):
    _owner, owner_tokens = await _create_verified_user_and_tokens("del-owner@test.com")
    _stranger, stranger_tokens = await _create_verified_user_and_tokens(
        "del-stranger@test.com"
    )

    created = await inprocess_client.post(
        "/channels",
        json={"name": "Guarded", "slug": "guarded"},
        headers=_auth(owner_tokens["access_token"]),
    )
    assert created.status_code == 201, created.text
    channel_id = created.json()["data"]["id"]

    refused = await inprocess_client.delete(
        f"/channels/{channel_id}", headers=_auth(stranger_tokens["access_token"])
    )
    assert refused.status_code == 403, refused.text
    assert await ChannelDocument.get(channel_id) is not None
