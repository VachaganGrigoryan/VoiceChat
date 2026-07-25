"""Legacy-shape views over relationship documents.

`ParticipantDocument`, `SpaceMemberDocument`, and `JoinRequestDocument` no
longer back any collection — conversation participation, space membership, and
join requests are all `Relationship(kind=membership)` rows now. Repositories
still hand those document types to their services, so these adapters project a
relationship into the legacy shape and keep the new field layout from leaking
into every caller. The returned documents are views: they are never inserted.
"""

from __future__ import annotations

from typing import Mapping, Sequence

from app.db.models import (
    JoinRequestDocument,
    ParticipantDocument,
    RelationshipDocument,
    SpaceMemberDocument,
)
from app.db.models.relationship import RelationshipTargetType

# The role a new member gets when no role is named. `resource-authorization`
# made `role_ids` a list of `RoleDocument` ids, so this is the *name* of the
# seeded system role to resolve, not a value ever written to `role_ids`.
DEFAULT_ROLE = "Member"

REQUEST_STATUS_BY_RELATIONSHIP_STATUS: dict[str, str] = {
    "pending": "pending",
    "active": "approved",
    "declined": "rejected",
    "revoked": "rejected",
}
RELATIONSHIP_STATUS_BY_REQUEST_STATUS: dict[str, str] = {
    "pending": "pending",
    "approved": "active",
    "rejected": "declined",
}


def membership_filter(
    *, target_type: RelationshipTargetType, target_id: str, user_id: str
) -> dict:
    return {
        "kind": "membership",
        "target_type": target_type,
        "target_id": str(target_id),
        "user_id": str(user_id),
    }


def _role_of(
    relationship: RelationshipDocument, role_names: Mapping[str, str] | None
) -> str | None:
    """The highest-priority role's *name*, or None when the member holds none.

    Names come from the `roles` collection; callers that don't resolve them get
    `None` rather than a raw id, so no id ever leaks into an API response.
    """
    if not relationship.role_ids or not role_names:
        return None
    for role_id in relationship.role_ids:
        name = role_names.get(str(role_id))
        if name is not None:
            return name
    return None


async def resolve_role_names(
    relationships: Sequence[RelationshipDocument],
) -> dict[str, str]:
    """Batch role id -> name for a set of memberships (one cached lookup)."""
    role_ids = [
        str(role_id)
        for relationship in relationships
        for role_id in relationship.role_ids
    ]
    if not role_ids:
        return {}
    from app.modules.authorization.repository import RolesRepository

    return await RolesRepository().names_by_id(role_ids)


def to_participant(
    relationship: RelationshipDocument,
    *,
    role_names: Mapping[str, str] | None = None,
) -> ParticipantDocument:
    """Project a conversation membership into the participant shape."""
    state = relationship.state
    participant = ParticipantDocument(
        conversation_id=str(relationship.target_id),
        user_id=str(relationship.user_id),
        role=_role_of(relationship, role_names),
        permissions=(
            {name: True for name in relationship.permission_overrides.allow}
            | {name: False for name in relationship.permission_overrides.deny}
            if relationship.permission_overrides is not None
            else None
        ),
        joined_at=relationship.activated_at or relationship.requested_at,
        last_read_at=state.last_read_at,
        last_read_message_id=state.last_read_message_id,
        notification_level=state.notification_level,
        muted_until=state.muted_until,
        archived=state.archived,
        pinned=state.pinned,
        folder=state.folder,
        invited_by=(
            str(relationship.initiated_by)
            if relationship.initiation == "invite"
            else None
        ),
        draft_text=state.draft_text,
        draft_updated_at=state.draft_updated_at,
        muted=state.notification_level == "none",
        hidden=state.hidden,
        created_at=relationship.created_at,
        updated_at=relationship.updated_at,
    )
    participant.id = relationship.id
    return participant


def to_space_member(
    relationship: RelationshipDocument,
    *,
    role_names: Mapping[str, str] | None = None,
) -> SpaceMemberDocument:
    """Project a space membership into the space-member shape."""
    member = SpaceMemberDocument(
        space_id=str(relationship.target_id),
        user_id=str(relationship.user_id),
        role=_role_of(relationship, role_names),
        joined_at=relationship.activated_at or relationship.requested_at,
        created_at=relationship.created_at,
        updated_at=relationship.updated_at,
    )
    member.id = relationship.id
    return member


def to_join_request(relationship: RelationshipDocument) -> JoinRequestDocument:
    """Project a membership into the join-request shape.

    A pending membership *is* the join request (design §5), so `pending`,
    `active`, and `declined`/`revoked` map onto the legacy
    `pending`/`approved`/`rejected` trio.
    """
    request = JoinRequestDocument(
        target_type=relationship.target_type,
        target_id=str(relationship.target_id),
        user_id=str(relationship.user_id),
        status=REQUEST_STATUS_BY_RELATIONSHIP_STATUS[relationship.status],
        invite_code=getattr(relationship, "invite_code", None),
        responded_at=relationship.activated_at or relationship.ended_at,
        created_at=relationship.created_at,
        updated_at=relationship.updated_at,
    )
    request.id = relationship.id
    return request
