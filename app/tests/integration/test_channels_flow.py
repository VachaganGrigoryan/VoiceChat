from __future__ import annotations

import pytest

from app.tests.integration.test_realtime_socket import (
    _create_verified_user_and_tokens,
)


def _auth(access_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {access_token}"}


# `core-resource-model` narrows Conversation to dm|group: channels are a first-class
# resource in the `channels` collection, not a conversation type. The channel *behavior*
# this file used to cover (public slug discovery, slug uniqueness per owner, posting
# policy enforcement) re-lands in `channels-and-profile-feed` against ChannelDocument and
# the /channels routes. What remains here is the narrowed write contract.


@pytest.mark.asyncio
async def test_channel_creation_as_conversation_is_rejected(inprocess_client):
    owner, owner_tokens = await _create_verified_user_and_tokens("chan-owner@test.com")

    resp = await inprocess_client.post(
        "/conversations/channels",
        json={
            "title": "Announcements",
            "description": "Company news",
            "visibility": "public",
            "posting_policy": "admins",
            "slug": "announcements",
        },
        headers=_auth(owner_tokens["access_token"]),
    )

    assert resp.status_code == 400, resp.text
    assert resp.json()["error"]["code"] == "INVALID_CONVERSATION_TYPE"


@pytest.mark.asyncio
async def test_no_channel_conversation_is_persisted(inprocess_client):
    """The rejected request must not leave a type=channel row behind."""
    owner, owner_tokens = await _create_verified_user_and_tokens("chan-owner2@test.com")

    await inprocess_client.post(
        "/conversations/channels",
        json={"title": "News", "visibility": "public", "slug": "news"},
        headers=_auth(owner_tokens["access_token"]),
    )

    from app.db.models import ConversationDocument

    assert await ConversationDocument.find({"type": "channel"}).count() == 0


@pytest.mark.asyncio
async def test_group_creation_still_works(inprocess_client):
    """Guard against the narrowing over-reaching into the surviving conversation types."""
    owner, owner_tokens = await _create_verified_user_and_tokens("chan-grp-o@test.com")
    member, _member_tokens = await _create_verified_user_and_tokens("chan-grp-m@test.com")
    from app.tests.integration.test_realtime_socket import _grant_chat_permission

    await _grant_chat_permission(str(owner["_id"]), str(member["_id"]))

    created = await inprocess_client.post(
        "/conversations/groups",
        json={"title": "Broadcast", "participant_ids": [str(member["_id"])]},
        headers=_auth(owner_tokens["access_token"]),
    )
    assert created.status_code == 201, created.text
    assert created.json()["data"]["type"] == "group"
