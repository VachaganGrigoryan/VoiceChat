from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from app.core.errors import AppError
from app.db.models import RelationshipDocument
from app.modules.relationships.connections import ConnectionService
from app.modules.relationships.follows import FollowService
from app.modules.relationships.memberships import MembershipService
from app.modules.relationships.repository import pair_id_for
from app.modules.relationships.service import RelationshipService

USER_A = "6501f77bd4a1c2b3e4f50001"
USER_B = "6501f77bd4a1c2b3e4f50002"
SPACE_ID = "6501f77bd4a1c2b3e4f50010"
CHANNEL_ID = "6501f77bd4a1c2b3e4f50011"
CONVERSATION_ID = "6501f77bd4a1c2b3e4f50012"
REL_ID = "6501f77bd4a1c2b3e4f50099"


def make_relationship(**overrides) -> RelationshipDocument:
    now = datetime.now(UTC)
    fields = {
        "kind": "connection",
        "user_id": USER_A,
        "target_type": "user",
        "target_id": USER_B,
        "status": "pending",
        "initiation": "request",
        "initiated_by": USER_A,
        "pair_id": pair_id_for(USER_A, USER_B),
        "requested_at": now,
        "created_at": now,
        "updated_at": now,
    }
    fields.update(overrides)
    doc = RelationshipDocument(**fields)
    doc.id = overrides.pop("id", None) or doc.id
    return doc


@pytest.fixture
def engine():
    repo = AsyncMock()
    repo.find_connection.return_value = None
    repo.find_edge.return_value = None
    return RelationshipService(repo=repo), repo


# --- 5.1 lifecycle transitions ----------------------------------------------


@pytest.mark.asyncio
async def test_request_opens_a_pending_connection(engine):
    service, repo = engine
    repo.create.return_value = make_relationship()

    await service.request(
        kind="connection", user_id=USER_A, target_type="user", target_id=USER_B
    )

    kwargs = repo.create.call_args.kwargs
    assert kwargs["status"] == "pending"
    assert kwargs["initiation"] == "request"
    assert kwargs["initiated_by"] == USER_A
    assert kwargs["pair_id"] == pair_id_for(USER_A, USER_B)


@pytest.mark.asyncio
async def test_invite_records_the_inviting_authority(engine):
    service, repo = engine
    repo.create.return_value = make_relationship(
        kind="membership", target_type="space", target_id=SPACE_ID, pair_id=None
    )

    await service.invite(
        kind="membership",
        user_id=USER_B,
        target_type="space",
        target_id=SPACE_ID,
        invited_by=USER_A,
    )

    kwargs = repo.create.call_args.kwargs
    assert kwargs["initiation"] == "invite"
    assert kwargs["initiated_by"] == USER_A
    assert kwargs["status"] == "pending"


@pytest.mark.asyncio
async def test_accept_activates_and_records_approver(engine):
    service, repo = engine
    repo.find_by_id.return_value = make_relationship(status="pending")
    repo.set_status.return_value = make_relationship(
        status="active", approved_by=USER_B, activated_at=datetime.now(UTC)
    )

    result = await service.accept(relationship_id=REL_ID, approved_by=USER_B)

    assert result.status == "active"
    assert result.approved_by == USER_B
    assert result.activated_at is not None
    kwargs = repo.set_status.call_args.kwargs
    assert kwargs["status"] == "active"
    assert kwargs["approved_by"] == USER_B
    # CAS on the prior status so two concurrent accepts cannot both win.
    assert kwargs["expected_status"] == "pending"


@pytest.mark.asyncio
async def test_accept_rejects_a_non_pending_relationship(engine):
    service, repo = engine
    repo.find_by_id.return_value = make_relationship(status="active")

    with pytest.raises(AppError) as exc:
        await service.accept(relationship_id=REL_ID, approved_by=USER_B)

    assert exc.value.code == "RELATIONSHIP_NOT_PENDING"
    assert exc.value.status_code == 409
    repo.set_status.assert_not_called()


@pytest.mark.asyncio
async def test_decline_marks_declined(engine):
    service, repo = engine
    repo.find_by_id.return_value = make_relationship(status="pending")
    repo.set_status.return_value = make_relationship(
        status="declined", ended_at=datetime.now(UTC)
    )

    result = await service.decline(relationship_id=REL_ID, declined_by=USER_B)

    assert result.status == "declined"
    assert result.ended_at is not None


@pytest.mark.asyncio
async def test_revoke_ends_an_active_relationship(engine):
    service, repo = engine
    repo.find_by_id.return_value = make_relationship(status="active")
    repo.set_status.return_value = make_relationship(
        status="revoked", ended_at=datetime.now(UTC)
    )

    result = await service.revoke(relationship_id=REL_ID)

    assert result.status == "revoked"
    assert result.ended_at is not None


@pytest.mark.asyncio
async def test_revoke_is_idempotent(engine):
    service, repo = engine
    repo.find_by_id.return_value = make_relationship(status="revoked")

    result = await service.revoke(relationship_id=REL_ID)

    assert result.status == "revoked"
    repo.set_status.assert_not_called()


@pytest.mark.asyncio
async def test_re_requesting_a_declined_edge_reuses_its_document(engine):
    """The unique index means a closed edge still occupies the slot."""
    service, repo = engine
    declined = make_relationship(status="declined")
    declined.id = None
    repo.find_connection.return_value = declined
    repo.reopen.return_value = make_relationship(status="pending")

    await service.request(
        kind="connection", user_id=USER_A, target_type="user", target_id=USER_B
    )

    repo.reopen.assert_awaited_once()
    repo.create.assert_not_called()


@pytest.mark.asyncio
async def test_requesting_an_active_edge_conflicts(engine):
    service, repo = engine
    repo.find_connection.return_value = make_relationship(status="active")

    with pytest.raises(AppError) as exc:
        await service.request(
            kind="connection", user_id=USER_A, target_type="user", target_id=USER_B
        )

    assert exc.value.code == "RELATIONSHIP_ALREADY_ACTIVE"
    assert exc.value.status_code == 409


# --- 5.1 per-kind validation and invariants ---------------------------------


@pytest.mark.parametrize(
    ("kind", "target_type"),
    [
        ("connection", "channel"),
        ("connection", "space"),
        ("follow", "space"),
        ("follow", "conversation"),
        ("membership", "user"),
    ],
)
def test_validate_rejects_disallowed_target_types(engine, kind, target_type):
    service, _ = engine

    with pytest.raises(AppError) as exc:
        service.validate(
            kind=kind,
            user_id=USER_A,
            target_type=target_type,
            target_id=SPACE_ID,
            pair_id=pair_id_for(USER_A, USER_B),
        )

    assert exc.value.code == "RELATIONSHIP_INVALID_TARGET"


@pytest.mark.parametrize(
    ("kind", "target_type", "target_id"),
    [
        ("connection", "user", USER_B),
        ("follow", "user", USER_B),
        ("follow", "channel", CHANNEL_ID),
        ("membership", "conversation", CONVERSATION_ID),
        ("membership", "channel", CHANNEL_ID),
        ("membership", "space", SPACE_ID),
    ],
)
def test_validate_accepts_allowed_target_types(engine, kind, target_type, target_id):
    service, _ = engine

    service.validate(
        kind=kind,
        user_id=USER_A,
        target_type=target_type,
        target_id=target_id,
        pair_id=pair_id_for(USER_A, USER_B) if kind == "connection" else None,
    )


def test_connection_requires_a_pair_id(engine):
    service, _ = engine

    with pytest.raises(AppError) as exc:
        service.validate(
            kind="connection", user_id=USER_A, target_type="user", target_id=USER_B
        )

    assert exc.value.code == "RELATIONSHIP_MISSING_PAIR_ID"


def test_a_user_cannot_relate_to_themselves(engine):
    service, _ = engine

    with pytest.raises(AppError) as exc:
        service.validate(
            kind="follow", user_id=USER_A, target_type="user", target_id=USER_A
        )

    assert exc.value.code == "RELATIONSHIP_SELF_TARGET"


def test_pair_id_is_canonical_regardless_of_order():
    assert pair_id_for(USER_A, USER_B) == pair_id_for(USER_B, USER_A)


@pytest.mark.asyncio
async def test_list_requires_a_user_or_a_full_target(engine):
    service, _ = engine

    with pytest.raises(AppError) as exc:
        await service.list(kind="membership")

    assert exc.value.code == "RELATIONSHIP_QUERY_INVALID"


# --- 5.2 connection is not follow -------------------------------------------


@pytest.mark.asyncio
async def test_following_does_not_create_a_connection():
    repo = AsyncMock()
    repo.find_edge.return_value = None
    repo.create.return_value = make_relationship(
        kind="follow", pair_id=None, status="active"
    )
    service = FollowService(repo=repo, engine=RelationshipService(repo=repo))
    service.requires_approval = AsyncMock(return_value=False)

    await service.follow(user_id=USER_A, target_type="user", target_id=USER_B)

    kwargs = repo.create.call_args.kwargs
    assert kwargs["kind"] == "follow"
    assert kwargs["pair_id"] is None
    repo.find_connection.assert_not_called()


@pytest.mark.asyncio
async def test_follow_back_is_a_second_independent_document():
    repo = AsyncMock()
    repo.find_edge.return_value = None
    repo.create.side_effect = [
        make_relationship(kind="follow", pair_id=None, status="active"),
        make_relationship(
            kind="follow",
            user_id=USER_B,
            target_id=USER_A,
            pair_id=None,
            status="active",
        ),
    ]
    service = FollowService(repo=repo, engine=RelationshipService(repo=repo))
    service.requires_approval = AsyncMock(return_value=False)

    await service.follow(user_id=USER_A, target_type="user", target_id=USER_B)
    await service.follow(user_id=USER_B, target_type="user", target_id=USER_A)

    assert repo.create.await_count == 2
    first, second = (call.kwargs for call in repo.create.await_args_list)
    assert (first["user_id"], first["target_id"]) == (USER_A, USER_B)
    assert (second["user_id"], second["target_id"]) == (USER_B, USER_A)


@pytest.mark.asyncio
async def test_follow_of_a_public_target_is_active_immediately():
    repo = AsyncMock()
    repo.find_edge.return_value = None
    repo.create.return_value = make_relationship(
        kind="follow", pair_id=None, status="active"
    )
    service = FollowService(repo=repo, engine=RelationshipService(repo=repo))
    service.requires_approval = AsyncMock(return_value=False)

    await service.follow(user_id=USER_A, target_type="user", target_id=USER_B)

    assert repo.create.call_args.kwargs["status"] == "active"


@pytest.mark.asyncio
async def test_follow_of_a_private_target_stays_pending():
    repo = AsyncMock()
    repo.find_edge.return_value = None
    repo.create.return_value = make_relationship(kind="follow", pair_id=None)
    service = FollowService(repo=repo, engine=RelationshipService(repo=repo))
    service.requires_approval = AsyncMock(return_value=True)

    await service.follow(user_id=USER_A, target_type="user", target_id=USER_B)

    assert repo.create.call_args.kwargs["status"] == "pending"


@pytest.mark.asyncio
async def test_connecting_does_not_create_a_follow():
    repo = AsyncMock()
    repo.find_connection.return_value = None
    repo.create.return_value = make_relationship()
    service = ConnectionService(repo=repo, engine=RelationshipService(repo=repo))
    service.is_blocked = AsyncMock(return_value=False)

    await service.request(from_user_id=USER_A, to_user_id=USER_B)

    assert repo.create.await_count == 1
    assert repo.create.call_args.kwargs["kind"] == "connection"


@pytest.mark.asyncio
async def test_connection_request_validates_target_and_creates_notification():
    repo = AsyncMock()
    repo.find_connection.return_value = None
    repo.create.return_value = make_relationship()
    users_repo = AsyncMock()
    users_repo.find_by_id.return_value = {"_id": USER_B}
    notifications = AsyncMock()
    service = ConnectionService(
        repo=repo,
        engine=RelationshipService(repo=repo),
        users_repo=users_repo,
        notifications_service=notifications,
    )
    service.is_blocked = AsyncMock(return_value=False)

    await service.request(from_user_id=USER_A, to_user_id=USER_B)

    users_repo.find_by_id.assert_awaited_once_with(USER_B)
    notifications.create_notification.assert_awaited_once_with(
        user_id=USER_B,
        kind="connection_request",
        actor_user_id=USER_A,
        resource_type="user",
        resource_id=USER_A,
        data={"peer_user_id": USER_A},
    )


@pytest.mark.asyncio
async def test_duplicate_pending_connection_does_not_duplicate_notification():
    pending = make_relationship()
    repo = AsyncMock()
    repo.find_connection.return_value = pending
    users_repo = AsyncMock()
    users_repo.find_by_id.return_value = {"_id": USER_B}
    notifications = AsyncMock()
    service = ConnectionService(
        repo=repo,
        engine=RelationshipService(repo=repo),
        users_repo=users_repo,
        notifications_service=notifications,
    )
    service.is_blocked = AsyncMock(return_value=False)

    result = await service.request(from_user_id=USER_A, to_user_id=USER_B)

    assert result is pending
    notifications.create_notification.assert_not_awaited()


@pytest.mark.asyncio
async def test_only_the_target_may_accept_a_connection():
    repo = AsyncMock()
    repo.find_by_id.return_value = make_relationship(status="pending")
    service = ConnectionService(repo=repo, engine=RelationshipService(repo=repo))

    with pytest.raises(AppError) as exc:
        await service.accept(user_id=USER_A, relationship_id=REL_ID)

    assert exc.value.code == "CONNECTION_FORBIDDEN"


@pytest.mark.asyncio
async def test_accepting_connection_notifies_requester():
    pending = make_relationship(status="pending")
    active = make_relationship(
        status="active",
        approved_by=USER_B,
        activated_at=datetime.now(UTC),
    )
    repo = AsyncMock()
    repo.find_by_id.return_value = pending
    repo.set_status.return_value = active
    notifications = AsyncMock()
    service = ConnectionService(
        repo=repo,
        engine=RelationshipService(repo=repo),
        notifications_service=notifications,
    )

    result = await service.accept(user_id=USER_B, relationship_id=REL_ID)

    assert result.status == "active"
    notifications.create_notification.assert_awaited_once_with(
        user_id=USER_A,
        kind="connection_accepted",
        actor_user_id=USER_B,
        resource_type="user",
        resource_id=USER_B,
        data={"peer_user_id": USER_B},
    )


@pytest.mark.asyncio
async def test_connection_state_uses_relationship_status_and_direction():
    repo = AsyncMock()
    repo.find_connection.return_value = make_relationship(status="pending")
    service = ConnectionService(repo=repo, engine=RelationshipService(repo=repo))
    service._get_block_state = AsyncMock(return_value=(False, False))

    incoming = await service.get_connection_state(
        viewer_user_id=USER_B,
        peer_user_id=USER_A,
    )

    assert incoming.connection_status == "pending"
    assert incoming.direction == "incoming"
    assert incoming.chat_allowed is False
    assert incoming.can_ping is False


@pytest.mark.asyncio
async def test_block_state_overrides_active_connection():
    repo = AsyncMock()
    repo.find_connection.return_value = make_relationship(status="active")
    service = ConnectionService(repo=repo, engine=RelationshipService(repo=repo))
    service._get_block_state = AsyncMock(return_value=(True, False))

    state = await service.get_connection_state(
        viewer_user_id=USER_A,
        peer_user_id=USER_B,
    )

    assert state.connection_status == "blocked"
    assert state.blocked_by_me is True
    assert state.chat_allowed is False


@pytest.mark.asyncio
async def test_active_connection_permits_a_dm():
    repo = AsyncMock()
    repo.find_connection.return_value = make_relationship(status="active")
    service = ConnectionService(repo=repo, engine=RelationshipService(repo=repo))
    service.is_blocked = AsyncMock(return_value=False)

    await service.ensure_can_message(sender_id=USER_A, receiver_id=USER_B)


@pytest.mark.asyncio
async def test_pending_connection_does_not_permit_a_dm():
    repo = AsyncMock()
    repo.find_connection.return_value = make_relationship(status="pending")
    service = ConnectionService(repo=repo, engine=RelationshipService(repo=repo))
    service.is_blocked = AsyncMock(return_value=False)

    with pytest.raises(AppError) as exc:
        await service.ensure_can_message(sender_id=USER_A, receiver_id=USER_B)

    assert exc.value.code == "CHAT_PERMISSION_REQUIRED"


@pytest.mark.asyncio
async def test_blocked_pair_cannot_message():
    repo = AsyncMock()
    service = ConnectionService(repo=repo, engine=RelationshipService(repo=repo))
    service.is_blocked = AsyncMock(return_value=True)

    with pytest.raises(AppError) as exc:
        await service.ensure_can_message(sender_id=USER_A, receiver_id=USER_B)

    assert exc.value.code == "CHAT_BLOCKED"


# --- 5.3 only active membership grants participation ------------------------


@pytest.fixture
def memberships():
    repo = AsyncMock()
    return MembershipService(repo=repo, engine=RelationshipService(repo=repo)), repo


@pytest.mark.parametrize("status", ["pending", "declined", "revoked"])
@pytest.mark.asyncio
async def test_non_active_membership_does_not_participate(memberships, status):
    service, repo = memberships
    repo.find_edge.return_value = make_relationship(
        kind="membership", target_type="space", target_id=SPACE_ID, status=status,
        pair_id=None,
    )

    assert not await service.is_active_member(
        user_id=USER_A, target_type="space", target_id=SPACE_ID
    )


@pytest.mark.asyncio
async def test_active_membership_participates(memberships):
    service, repo = memberships
    repo.find_edge.return_value = make_relationship(
        kind="membership", target_type="space", target_id=SPACE_ID, status="active",
        pair_id=None,
    )

    assert await service.is_active_member(
        user_id=USER_A, target_type="space", target_id=SPACE_ID
    )


@pytest.mark.asyncio
async def test_missing_membership_does_not_participate(memberships):
    service, repo = memberships
    repo.find_edge.return_value = None

    assert not await service.is_active_member(
        user_id=USER_A, target_type="space", target_id=SPACE_ID
    )


@pytest.mark.asyncio
async def test_open_join_policy_activates_immediately(memberships):
    service, repo = memberships
    repo.find_edge.return_value = None
    repo.create.return_value = make_relationship(
        kind="membership", target_type="space", target_id=SPACE_ID, status="active",
        pair_id=None,
    )
    service.join_policy_for = AsyncMock(return_value="open")
    service._default_role_ids = AsyncMock(return_value=None)

    await service.request_join(
        user_id=USER_A, target_type="space", target_id=SPACE_ID
    )

    assert repo.create.call_args.kwargs["status"] == "active"


@pytest.mark.asyncio
async def test_approval_join_policy_stays_pending(memberships):
    service, repo = memberships
    repo.find_edge.return_value = None
    repo.create.return_value = make_relationship(
        kind="membership", target_type="space", target_id=SPACE_ID, pair_id=None
    )
    service.join_policy_for = AsyncMock(return_value="approval")
    service._default_role_ids = AsyncMock(return_value=None)

    await service.request_join(
        user_id=USER_A, target_type="space", target_id=SPACE_ID
    )

    kwargs = repo.create.call_args.kwargs
    assert kwargs["status"] == "pending"
    assert kwargs["initiation"] == "request"


@pytest.mark.parametrize(
    ("policy", "code"),
    [("invite_only", "JOIN_INVITE_ONLY"), ("closed", "JOIN_CLOSED")],
)
@pytest.mark.asyncio
async def test_closed_and_invite_only_reject_join_requests(memberships, policy, code):
    service, repo = memberships
    service.join_policy_for = AsyncMock(return_value=policy)

    with pytest.raises(AppError) as exc:
        await service.request_join(
            user_id=USER_A, target_type="space", target_id=SPACE_ID
        )

    assert exc.value.code == code
    repo.create.assert_not_called()


@pytest.mark.asyncio
async def test_only_the_invitee_may_accept_their_membership(memberships):
    service, repo = memberships
    repo.find_by_id.return_value = make_relationship(
        kind="membership", target_type="space", target_id=SPACE_ID, pair_id=None
    )

    with pytest.raises(AppError) as exc:
        await service.accept(user_id=USER_B, relationship_id=REL_ID)

    assert exc.value.code == "MEMBERSHIP_FORBIDDEN"


@pytest.mark.asyncio
async def test_membership_state_updates_are_whitelisted(memberships):
    service, repo = memberships

    await service.update_state(
        user_id=USER_A,
        target_type="conversation",
        target_id=CONVERSATION_ID,
        updates={"pinned": True, "role_ids": ["owner"], "status": "active"},
    )

    assert repo.update_state_for_edge.call_args.kwargs["updates"] == {"pinned": True}


@pytest.mark.asyncio
async def test_membership_state_update_with_no_allowed_field_is_a_no_op(memberships):
    service, repo = memberships

    result = await service.update_state(
        user_id=USER_A,
        target_type="conversation",
        target_id=CONVERSATION_ID,
        updates={"status": "active"},
    )

    assert result is None
    repo.update_state_for_edge.assert_not_called()
