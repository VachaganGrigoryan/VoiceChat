from __future__ import annotations

from typing import Mapping

# Fine-grained rights that a participant may hold. The base role grants a default
# set; an optional per-participant ``permissions`` map refines (only ever narrows)
# those defaults — it never grants a right the role does not already imply.
CONVERSATION_RIGHTS = (
    "can_pin",
    "can_invite",
    "can_delete",
    "can_restrict",
    "can_manage",
)

# Role -> rights granted by default (before any per-participant refinement).
_ROLE_DEFAULT_RIGHTS: dict[str, set[str]] = {
    "owner": set(CONVERSATION_RIGHTS),
    "admin": set(CONVERSATION_RIGHTS),
    "member": set(),
    "subscriber": set(),
}


def role_allows(role: str, right: str) -> bool:
    return right in _ROLE_DEFAULT_RIGHTS.get(role, set())


def participant_can(
    *, role: str, permissions: Mapping[str, bool] | None, right: str
) -> bool:
    """Whether a participant holds ``right``.

    Effective access is the base-role default AND the map value when present, so
    the map can only revoke a role's default right — never grant one beyond it.
    An absent map (or an absent key) falls back to the role default.
    """
    if not role_allows(role, right):
        return False
    if permissions is None:
        return True
    return bool(permissions.get(right, True))
