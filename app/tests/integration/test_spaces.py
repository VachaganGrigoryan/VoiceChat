from __future__ import annotations

import pytest

from app.tests.integration.test_realtime_socket import (
    _create_verified_user_and_tokens,
    _grant_chat_permission,
)

def _auth(access_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {access_token}"}

@pytest.mark.asyncio
async def test_spaces_crud_and_slug_uniqueness(inprocess_client):
    owner, owner_tokens = await _create_verified_user_and_tokens("space-owner@test.com")
    
    # 1. Create a space
    resp = await inprocess_client.post(
        "/spaces",
        json={
            "name": "My Workspace",
            "slug": "my-workspace",
            "kind": "workspace",
            "visibility": "private",
        },
        headers=_auth(owner_tokens["access_token"]),
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()["data"]
    assert data["name"] == "My Workspace"
    assert data["slug"] == "my-workspace"
    assert data["kind"] == "workspace"
    assert data["created_by"] == str(owner["_id"])
    
    # 2. Slug conflict
    other, other_tokens = await _create_verified_user_and_tokens("space-other@test.com")
    resp_conflict = await inprocess_client.post(
        "/spaces",
        json={
            "name": "Another Workspace",
            "slug": "my-workspace",
            "kind": "workspace",
            "visibility": "private",
        },
        headers=_auth(other_tokens["access_token"]),
    )
    assert resp_conflict.status_code == 409, resp_conflict.text
    assert resp_conflict.json()["error"]["code"] == "SPACE_SLUG_TAKEN"

    # 3. List my spaces
    resp_list = await inprocess_client.get(
        "/spaces/me",
        headers=_auth(owner_tokens["access_token"]),
    )
    assert resp_list.status_code == 200
    spaces = resp_list.json()["data"]
    # `/spaces/me` materializes the default Vogi space on first read and sorts
    # it first, so an owner of one space sees two.
    assert len(spaces) == 2
    assert spaces[0]["slug"] == "vogi"
    assert spaces[0]["is_default"] is True
    assert [space["slug"] for space in spaces[1:]] == ["my-workspace"]

@pytest.mark.asyncio
async def test_space_invite_and_redeem_flow(inprocess_client):
    owner, owner_tokens = await _create_verified_user_and_tokens("space-owner2@test.com")
    user, user_tokens = await _create_verified_user_and_tokens("space-user@test.com")

    # Create space
    resp_space = await inprocess_client.post(
        "/spaces",
        json={"name": "Team Space", "slug": "team-space"},
        headers=_auth(owner_tokens["access_token"]),
    )
    space_id = resp_space.json()["data"]["id"]

    # 1. Non-manager cannot create invite
    resp_fail = await inprocess_client.post(
        f"/spaces/{space_id}/invites",
        json={"approval_required": False},
        headers=_auth(user_tokens["access_token"]),
    )
    assert resp_fail.status_code == 403

    # 2. Create invite (direct join)
    resp_invite = await inprocess_client.post(
        f"/spaces/{space_id}/invites",
        json={"approval_required": False},
        headers=_auth(owner_tokens["access_token"]),
    )
    assert resp_invite.status_code == 201
    code = resp_invite.json()["data"]["code"]

    # 3. Redeem invite
    resp_redeem = await inprocess_client.post(
        f"/spaces/invites/{code}/redeem",
        headers=_auth(user_tokens["access_token"]),
    )
    assert resp_redeem.status_code == 200
    redeem_data = resp_redeem.json()["data"]
    assert redeem_data["status"] == "joined"
    assert redeem_data["space"]["id"] == space_id

    # 4. Create approval-gated invite
    resp_invite_gate = await inprocess_client.post(
        f"/spaces/{space_id}/invites",
        json={"approval_required": True},
        headers=_auth(owner_tokens["access_token"]),
    )
    code_gate = resp_invite_gate.json()["data"]["code"]

    # Redeem approval-gated invite
    user2, user2_tokens = await _create_verified_user_and_tokens("space-user2@test.com")
    resp_redeem_gate = await inprocess_client.post(
        f"/spaces/invites/{code_gate}/redeem",
        headers=_auth(user2_tokens["access_token"]),
    )
    assert resp_redeem_gate.status_code == 200
    redeem_gate_data = resp_redeem_gate.json()["data"]
    assert redeem_gate_data["status"] == "pending"
    assert redeem_gate_data["membership"]["status"] == "pending"
    req_id = redeem_gate_data["membership"]["id"]

    # Approve join request
    resp_approve = await inprocess_client.post(
        f"/spaces/{space_id}/join-requests/{req_id}/approve",
        headers=_auth(owner_tokens["access_token"]),
    )
    assert resp_approve.status_code == 200
    assert resp_approve.json()["data"]["status"] == "approved"

@pytest.mark.asyncio
async def test_ping_to_org_direct_join_request(inprocess_client):
    owner, owner_tokens = await _create_verified_user_and_tokens("space-owner3@test.com")
    user, user_tokens = await _create_verified_user_and_tokens("space-user3@test.com")

    # Create space
    resp_space = await inprocess_client.post(
        "/spaces",
        json={"name": "Org Space", "slug": "org-space"},
        headers=_auth(owner_tokens["access_token"]),
    )
    space_id = resp_space.json()["data"]["id"]

    # Direct request to join
    resp_join = await inprocess_client.post(
        f"/spaces/{space_id}/join",
        headers=_auth(user_tokens["access_token"]),
    )
    assert resp_join.status_code == 201
    req_data = resp_join.json()["data"]
    assert req_data["status"] == "pending"
    req_id = req_data["id"]

    # Approve direct join request
    resp_approve = await inprocess_client.post(
        f"/spaces/{space_id}/join-requests/{req_id}/approve",
        headers=_auth(owner_tokens["access_token"]),
    )
    assert resp_approve.status_code == 200
    assert resp_approve.json()["data"]["status"] == "approved"

@pytest.mark.asyncio
async def test_space_scoped_conversations_and_global_scope(inprocess_client):
    owner, owner_tokens = await _create_verified_user_and_tokens("space-owner4@test.com")
    user, user_tokens = await _create_verified_user_and_tokens("space-user4@test.com")
    outsider, _ = await _create_verified_user_and_tokens("space-outsider-dm@test.com")

    # Grant chat permission between them to allow conversation creation
    await _grant_chat_permission(str(owner["_id"]), str(user["_id"]))
    await _grant_chat_permission(str(owner["_id"]), str(outsider["_id"]))

    # Create space
    resp_space = await inprocess_client.post(
        "/spaces",
        json={"name": "Scoped Space", "slug": "scoped-space"},
        headers=_auth(owner_tokens["access_token"]),
    )
    space_id = resp_space.json()["data"]["id"]

    # 1. Cannot create space-scoped conversation if participants are not space members
    resp_conv_fail = await inprocess_client.post(
        "/conversations/groups",
        json={
            "title": "Scoped group",
            "participant_ids": [str(user["_id"])],
            "space_id": space_id,
        },
        headers=_auth(owner_tokens["access_token"]),
    )
    assert resp_conv_fail.status_code == 400
    assert resp_conv_fail.json()["error"]["code"] == "SPACE_MEMBER_ELIGIBILITY"

    # Make user a space member via direct add (simulate accepted invite)
    # Create direct invite
    resp_invite = await inprocess_client.post(
        f"/spaces/{space_id}/invites",
        json={"approval_required": False},
        headers=_auth(owner_tokens["access_token"]),
    )
    code = resp_invite.json()["data"]["code"]
    # User redeems
    await inprocess_client.post(
        f"/spaces/invites/{code}/redeem",
        headers=_auth(user_tokens["access_token"]),
    )

    # 2. Now conversation creation succeeds
    resp_conv = await inprocess_client.post(
        "/conversations/groups",
        json={
            "title": "Scoped group",
            "participant_ids": [str(user["_id"])],
            "space_id": space_id,
        },
        headers=_auth(owner_tokens["access_token"]),
    )
    assert resp_conv.status_code == 201
    conv_data = resp_conv.json()["data"]
    assert conv_data["space_id"] == space_id

    resp_member_dm = await inprocess_client.post(
        "/conversations",
        json={"peer_user_id": str(user["_id"])},
        headers=_auth(owner_tokens["access_token"]),
    )
    assert resp_member_dm.status_code == 200
    member_dm_id = resp_member_dm.json()["data"]["id"]

    resp_outsider_dm = await inprocess_client.post(
        "/conversations",
        json={"peer_user_id": str(outsider["_id"])},
        headers=_auth(owner_tokens["access_token"]),
    )
    assert resp_outsider_dm.status_code == 200
    outsider_dm_id = resp_outsider_dm.json()["data"]["id"]

    # 3. List conversations scoped to this space
    resp_list = await inprocess_client.get(
        f"/conversations?space_id={space_id}",
        headers=_auth(owner_tokens["access_token"]),
    )
    assert resp_list.status_code == 200
    convs = resp_list.json()["data"]
    scoped_ids = {conv["id"] for conv in convs}
    assert conv_data["id"] in scoped_ids
    assert member_dm_id in scoped_ids
    assert outsider_dm_id not in scoped_ids
    assert all(conv["space_id"] == space_id or conv["type"] == "dm" for conv in convs)

    # 4. Non-space member trying to list space conversations is rejected
    non_member, non_member_tokens = await _create_verified_user_and_tokens("non-member@test.com")
    resp_list_fail = await inprocess_client.get(
        f"/conversations?space_id={space_id}",
        headers=_auth(non_member_tokens["access_token"]),
    )
    assert resp_list_fail.status_code == 403

    # 5. Spaceless global scope conversations are unaffected and default
    # Create global (spaceless) conversation
    resp_global = await inprocess_client.post(
        "/conversations/groups",
        json={
            "title": "Global group",
            "participant_ids": [str(user["_id"])],
        },
        headers=_auth(owner_tokens["access_token"]),
    )
    assert resp_global.status_code == 201
    global_group_id = resp_global.json()["data"]["id"]
    assert resp_global.json()["data"]["space_id"] is None

    resp_scoped_after_global = await inprocess_client.get(
        f"/conversations?space_id={space_id}",
        headers=_auth(owner_tokens["access_token"]),
    )
    assert resp_scoped_after_global.status_code == 200
    assert global_group_id not in {
        conv["id"] for conv in resp_scoped_after_global.json()["data"]
    }

    # List global conversations (should show global channel only)
    resp_global_list = await inprocess_client.get(
        "/conversations",
        headers=_auth(owner_tokens["access_token"]),
    )
    assert resp_global_list.status_code == 200
    global_convs = resp_global_list.json()["data"]
    # Should only return conversations where space_id is null (our global group)
    assert any(c["id"] == global_group_id for c in global_convs)
    assert all(c["space_id"] is None for c in global_convs)


@pytest.mark.asyncio
async def test_vogi_space_group_creation_materializes_default_membership(inprocess_client):
    owner, owner_tokens = await _create_verified_user_and_tokens("vogi-owner@test.com")
    peer, _ = await _create_verified_user_and_tokens("vogi-peer@test.com")

    resp_spaces = await inprocess_client.get(
        "/spaces/me",
        headers=_auth(owner_tokens["access_token"]),
    )
    assert resp_spaces.status_code == 200
    vogi_space = next(
        space for space in resp_spaces.json()["data"] if space["slug"] == "vogi"
    )

    resp_group = await inprocess_client.post(
        "/conversations/groups",
        json={
            "title": "Vogi group",
            "participant_ids": [str(peer["_id"])],
            "space_id": vogi_space["id"],
        },
        headers=_auth(owner_tokens["access_token"]),
    )
    assert resp_group.status_code == 201, resp_group.text
    assert resp_group.json()["data"]["space_id"] == vogi_space["id"]


@pytest.mark.asyncio
async def test_direct_space_invite_and_redeem_flow(inprocess_client):
    from unittest.mock import patch
    owner, owner_tokens = await _create_verified_user_and_tokens("space-owner-direct@test.com")
    user, user_tokens = await _create_verified_user_and_tokens("space-user-direct@test.com")
    other_user, other_tokens = await _create_verified_user_and_tokens("space-other-direct@test.com")

    # Create space
    resp_space = await inprocess_client.post(
        "/spaces",
        json={"name": "Direct Invite Space", "slug": "direct-invite-space"},
        headers=_auth(owner_tokens["access_token"]),
    )
    space_id = resp_space.json()["data"]["id"]

    # 1. Invite user directly
    with patch("app.modules.spaces.router.emit_space_invite") as mock_emit:
        resp_invite = await inprocess_client.post(
            f"/spaces/{space_id}/invites/user",
            json={"user_id": str(user["_id"])},
            headers=_auth(owner_tokens["access_token"]),
        )
        assert resp_invite.status_code == 201
        code = resp_invite.json()["data"]["code"]
        assert resp_invite.json()["data"]["invitee_id"] == str(user["_id"])
        mock_emit.assert_called_once()
        _, kwargs = mock_emit.call_args
        assert kwargs["to_user_id"] == str(user["_id"])

    # Verify notification was created
    from app.db.models import NotificationDocument
    notification = await NotificationDocument.find_one(
        {"user_id": str(user["_id"]), "kind": "membership_invite"}
    )
    assert notification is not None
    assert notification.actor_user_id == str(owner["_id"])
    assert notification.resource_type == "space"
    assert notification.resource_id == space_id
    assert notification.data["space_id"] == space_id
    assert notification.data["code"] == code

    # 2. Other user tries to redeem direct invite -> Forbidden
    resp_redeem_fail = await inprocess_client.post(
        f"/spaces/invites/{code}/redeem",
        headers=_auth(other_tokens["access_token"]),
    )
    assert resp_redeem_fail.status_code == 403
    assert resp_redeem_fail.json()["error"]["code"] == "INVITE_FORBIDDEN"

    # 3. Invitee redeems direct invite -> Success
    resp_redeem_ok = await inprocess_client.post(
        f"/spaces/invites/{code}/redeem",
        headers=_auth(user_tokens["access_token"]),
    )
    assert resp_redeem_ok.status_code == 200
    assert resp_redeem_ok.json()["data"]["status"] == "joined"
