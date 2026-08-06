from __future__ import annotations

import pytest

from app.tests.integration.test_realtime_socket import (
    _create_verified_user_and_tokens,
    _grant_chat_permission,
)


def _auth(access_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {access_token}"}


async def _group_with_admin(client):
    owner, owner_tokens = await _create_verified_user_and_tokens("perm-o@test.com")
    admin, admin_tokens = await _create_verified_user_and_tokens("perm-a@test.com")
    await _grant_chat_permission(str(owner["_id"]), str(admin["_id"]))

    group = await client.post(
        "/conversations/groups",
        json={"title": "Ops", "participant_ids": [str(admin["_id"])]},
        headers=_auth(owner_tokens["access_token"]),
    )
    conversation_id = group.json()["data"]["id"]

    promote = await client.patch(
        f"/conversations/{conversation_id}/members/{admin['_id']}/role",
        json={"role": "Admin"},
        headers=_auth(owner_tokens["access_token"]),
    )
    assert promote.status_code == 200, promote.text
    return owner, owner_tokens, admin, admin_tokens, conversation_id


@pytest.mark.asyncio
async def test_permissions_map_refines_admin_invite_right(inprocess_client):
    owner, owner_tokens, admin, admin_tokens, conversation_id = await _group_with_admin(
        inprocess_client
    )

    # Admin can create invites by role default.
    allowed = await inprocess_client.post(
        f"/conversations/{conversation_id}/invites",
        json={},
        headers=_auth(admin_tokens["access_token"]),
    )
    assert allowed.status_code == 201, allowed.text

    # Owner revokes the dotted member.invite right for this admin.
    revoke = await inprocess_client.patch(
        f"/conversations/{conversation_id}/members/{admin['_id']}/permissions",
        json={"permissions": {"member.invite": False}},
        headers=_auth(owner_tokens["access_token"]),
    )
    assert revoke.status_code == 200, revoke.text
    assert revoke.json()["data"]["permissions"] == {"member.invite": False}

    # Now invite creation is denied...
    denied = await inprocess_client.post(
        f"/conversations/{conversation_id}/invites",
        json={},
        headers=_auth(admin_tokens["access_token"]),
    )
    assert denied.status_code == 403, denied.text

    # ...but other admin actions (rename) still work.
    rename = await inprocess_client.patch(
        f"/conversations/groups/{conversation_id}",
        json={"title": "Ops Renamed"},
        headers=_auth(admin_tokens["access_token"]),
    )
    assert rename.status_code == 200, rename.text


@pytest.mark.asyncio
async def test_clearing_permissions_restores_role_default(inprocess_client):
    owner, owner_tokens, admin, admin_tokens, conversation_id = await _group_with_admin(
        inprocess_client
    )

    await inprocess_client.patch(
        f"/conversations/{conversation_id}/members/{admin['_id']}/permissions",
        json={"permissions": {"member.invite": False}},
        headers=_auth(owner_tokens["access_token"]),
    )
    cleared = await inprocess_client.patch(
        f"/conversations/{conversation_id}/members/{admin['_id']}/permissions",
        json={"permissions": None},
        headers=_auth(owner_tokens["access_token"]),
    )
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["data"]["permissions"] is None

    restored = await inprocess_client.post(
        f"/conversations/{conversation_id}/invites",
        json={},
        headers=_auth(admin_tokens["access_token"]),
    )
    assert restored.status_code == 201, restored.text


@pytest.mark.asyncio
async def test_setting_permissions_requires_member_manage(inprocess_client):
    """Setting overrides is gated by `member.manage`, not by ownership.

    Ownership bypasses RBAC but is not the gate here: an Admin holds
    `member.manage` through its role and may set overrides, while a plain
    Member may not.
    """
    owner, owner_tokens, admin, admin_tokens, conversation_id = await _group_with_admin(
        inprocess_client
    )
    member, member_tokens = await _create_verified_user_and_tokens("perm-m@test.com")
    await _grant_chat_permission(str(owner["_id"]), str(member["_id"]))
    added = await inprocess_client.post(
        f"/conversations/{conversation_id}/members",
        json={"participant_ids": [str(member["_id"])]},
        headers=_auth(owner_tokens["access_token"]),
    )
    assert added.status_code == 201, added.text

    # An Admin holds member.manage by role default.
    by_admin = await inprocess_client.patch(
        f"/conversations/{conversation_id}/members/{member['_id']}/permissions",
        json={"permissions": {"member.invite": False}},
        headers=_auth(admin_tokens["access_token"]),
    )
    assert by_admin.status_code == 200, by_admin.text

    # A plain Member does not.
    by_member = await inprocess_client.patch(
        f"/conversations/{conversation_id}/members/{admin['_id']}/permissions",
        json={"permissions": {"member.invite": False}},
        headers=_auth(member_tokens["access_token"]),
    )
    assert by_member.status_code == 403, by_member.text


@pytest.mark.asyncio
async def test_unknown_permission_key_rejected(inprocess_client):
    owner, owner_tokens, admin, admin_tokens, conversation_id = await _group_with_admin(
        inprocess_client
    )

    resp = await inprocess_client.patch(
        f"/conversations/{conversation_id}/members/{admin['_id']}/permissions",
        json={"permissions": {"can_teleport": True}},
        headers=_auth(owner_tokens["access_token"]),
    )
    assert resp.status_code == 400, resp.text
    assert resp.json()["error"]["code"] == "INVALID_PERMISSIONS"
