from __future__ import annotations

from app.modules.conversations.permissions import participant_can


def test_absent_map_uses_role_defaults():
    assert participant_can(role="admin", permissions=None, right="can_invite") is True
    assert participant_can(role="member", permissions=None, right="can_invite") is False
    assert participant_can(role="owner", permissions=None, right="can_delete") is True
    assert (
        participant_can(role="subscriber", permissions=None, right="can_pin") is False
    )


def test_map_can_revoke_a_role_default():
    assert (
        participant_can(
            role="admin", permissions={"can_delete": False}, right="can_delete"
        )
        is False
    )
    # Other rights of the same admin remain intact.
    assert (
        participant_can(
            role="admin", permissions={"can_delete": False}, right="can_invite"
        )
        is True
    )


def test_map_never_grants_beyond_role_intent():
    # A member cannot be granted a right the role does not imply.
    assert (
        participant_can(
            role="member", permissions={"can_pin": True}, right="can_pin"
        )
        is False
    )
