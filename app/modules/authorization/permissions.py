"""The permission-string vocabulary (RefactoringPlan §52).

Permissions are dotted strings, never boolean fields, so roles stay data and new
capabilities need no schema change. Actions ending in ``.own`` are resolved
against authorship (``sender_id == user_id``) rather than granted outright; their
``.any`` sibling is the unrestricted form.
"""

from __future__ import annotations

RESOURCE_VIEW = "resource.view"
RESOURCE_MANAGE = "resource.manage"
RESOURCE_DELETE = "resource.delete"

MEMBER_VIEW = "member.view"
MEMBER_INVITE = "member.invite"
MEMBER_APPROVE = "member.approve"
MEMBER_REMOVE = "member.remove"
MEMBER_MANAGE = "member.manage"

ROLE_VIEW = "role.view"
ROLE_MANAGE = "role.manage"

MESSAGE_READ = "message.read"
MESSAGE_CREATE = "message.create"
MESSAGE_EDIT_OWN = "message.edit.own"
MESSAGE_EDIT_ANY = "message.edit.any"
MESSAGE_DELETE_OWN = "message.delete.own"
MESSAGE_DELETE_ANY = "message.delete.any"
MESSAGE_PIN = "message.pin"

THREAD_CREATE = "thread.create"
THREAD_REPLY = "thread.reply"

REACTION_CREATE = "reaction.create"
REACTION_DELETE_OWN = "reaction.delete.own"
REACTION_DELETE_ANY = "reaction.delete.any"

CHANNEL_CREATE = "channel.create"
CHANNEL_MANAGE = "channel.manage"
CHANNEL_DELETE = "channel.delete"

GROUP_CREATE = "group.create"
GROUP_MANAGE = "group.manage"

CALL_CREATE = "call.create"
CALL_MANAGE = "call.manage"

POLL_CREATE = "poll.create"
POLL_MANAGE = "poll.manage"

PERMISSIONS: frozenset[str] = frozenset(
    {
        RESOURCE_VIEW,
        RESOURCE_MANAGE,
        RESOURCE_DELETE,
        MEMBER_VIEW,
        MEMBER_INVITE,
        MEMBER_APPROVE,
        MEMBER_REMOVE,
        MEMBER_MANAGE,
        ROLE_VIEW,
        ROLE_MANAGE,
        MESSAGE_READ,
        MESSAGE_CREATE,
        MESSAGE_EDIT_OWN,
        MESSAGE_EDIT_ANY,
        MESSAGE_DELETE_OWN,
        MESSAGE_DELETE_ANY,
        MESSAGE_PIN,
        THREAD_CREATE,
        THREAD_REPLY,
        REACTION_CREATE,
        REACTION_DELETE_OWN,
        REACTION_DELETE_ANY,
        CHANNEL_CREATE,
        CHANNEL_MANAGE,
        CHANNEL_DELETE,
        GROUP_CREATE,
        GROUP_MANAGE,
        CALL_CREATE,
        CALL_MANAGE,
        POLL_CREATE,
        POLL_MANAGE,
    }
)

# Actions that only apply to content the actor authored. `can()` requires the
# caller to pass `is_own=True` before one of these can grant.
OWN_SCOPED_PERMISSIONS: frozenset[str] = frozenset(
    {
        MESSAGE_EDIT_OWN,
        MESSAGE_DELETE_OWN,
        REACTION_DELETE_OWN,
    }
)

# `.own` action -> the `.any` permission that also satisfies it. A moderator
# holding `message.delete.any` can delete their own message without also
# holding `message.delete.own`.
ANY_EQUIVALENT: dict[str, str] = {
    MESSAGE_EDIT_OWN: MESSAGE_EDIT_ANY,
    MESSAGE_DELETE_OWN: MESSAGE_DELETE_ANY,
    REACTION_DELETE_OWN: REACTION_DELETE_ANY,
}


def is_known_permission(permission: str) -> bool:
    return permission in PERMISSIONS


def is_own_scoped(permission: str) -> bool:
    return permission in OWN_SCOPED_PERMISSIONS


def unknown_permissions(permissions: list[str]) -> set[str]:
    """The subset of ``permissions`` outside the vocabulary (validation helper)."""
    return set(permissions) - PERMISSIONS
