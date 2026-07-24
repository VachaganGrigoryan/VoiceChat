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
    assert len(spaces) == 1
    assert spaces[0]["slug"] == "my-workspace"

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
        json={"requires_approval": False},
        headers=_auth(user_tokens["access_token"]),
    )
    assert resp_fail.status_code == 403

    # 2. Create invite (direct join)
    resp_invite = await inprocess_client.post(
        f"/spaces/{space_id}/invites",
        json={"requires_approval": False},
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
        json={"requires_approval": True},
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
    req_id = redeem_gate_data["join_request"]["id"]

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

    # Grant chat permission between them to allow conversation creation
    await _grant_chat_permission(str(owner["_id"]), str(user["_id"]))

    # Create space
    resp_space = await inprocess_client.post(
        "/spaces",
        json={"name": "Scoped Space", "slug": "scoped-space"},
        headers=_auth(owner_tokens["access_token"]),
    )
    space_id = resp_space.json()["data"]["id"]

    # 1. Cannot create space-scoped conversation if participants are not space members
    resp_conv_fail = await inprocess_client.post(
        "/conversations/channels",
        json={
            "title": "Scoped channel",
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
        json={"requires_approval": False},
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
        "/conversations/channels",
        json={
            "title": "Scoped channel",
            "participant_ids": [str(user["_id"])],
            "space_id": space_id,
        },
        headers=_auth(owner_tokens["access_token"]),
    )
    assert resp_conv.status_code == 201
    conv_data = resp_conv.json()["data"]
    assert conv_data["space_id"] == space_id

    # 3. List conversations scoped to this space
    resp_list = await inprocess_client.get(
        f"/conversations?space_id={space_id}",
        headers=_auth(owner_tokens["access_token"]),
    )
    assert resp_list.status_code == 200
    convs = resp_list.json()["data"]
    assert len(convs) == 1
    assert convs[0]["space_id"] == space_id

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
        "/conversations/channels",
        json={
            "title": "Global channel",
            "participant_ids": [str(user["_id"])],
        },
        headers=_auth(owner_tokens["access_token"]),
    )
    assert resp_global.status_code == 201
    assert resp_global.json()["data"]["space_id"] is None

    # List global conversations (should show global channel only)
    resp_global_list = await inprocess_client.get(
        "/conversations",
        headers=_auth(owner_tokens["access_token"]),
    )
    assert resp_global_list.status_code == 200
    global_convs = resp_global_list.json()["data"]
    # Should only return conversations where space_id is null (our global channel)
    assert any(c["id"] == resp_global.json()["data"]["id"] for c in global_convs)
    assert all(c["space_id"] is None for c in global_convs)
