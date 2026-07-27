from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from app.core.errors import AppError
from app.modules.authorization.roles import ROLE_MEMBER
from app.modules.spaces.service import SpacesService


@pytest.fixture
def service():
    repo = AsyncMock()
    notifications_service = AsyncMock()
    
    repo.get_membership = AsyncMock()
    relationships = AsyncMock()
    
    svc = SpacesService(
        repo=repo,
        notifications_service=notifications_service,
        relationships=relationships,
    )
    return svc, repo, notifications_service


@pytest.mark.asyncio
async def test_invite_user_success(service):
    svc, repo, notifications = service
    
    svc._require_can = AsyncMock()
    
    space = MagicMock()
    space.name = "My Test Space"
    repo.get_by_id.return_value = space
    repo.get_membership.return_value = None  # user not a member yet
    
    invite_doc = MagicMock()
    invite_doc.str_id = "invite123"
    invite_doc.target_id = "space123"
    invite_doc.code = "secretcode"
    invite_doc.created_by = "owner123"
    invite_doc.expires_at = None
    invite_doc.max_uses = 1
    invite_doc.uses = 0
    invite_doc.approval_required = False
    invite_doc.role_ids = []
    invite_doc.revoked = False
    invite_doc.invitee_id = "invitee123"
    
    repo.create_invite_link.return_value = invite_doc
    
    res = await svc.invite_user(
        actor_user_id="owner123",
        space_id="space123",
        user_id="invitee123",
    )
    
    assert res.id == "invite123"
    assert res.invitee_id == "invitee123"
    
    repo.create_invite_link.assert_called_once()
    notifications.create_notification.assert_called_once_with(
        user_id="invitee123",
        kind="membership_invite",
        actor_user_id="owner123",
        resource_type="space",
        resource_id="space123",
        data={
            "space_id": "space123",
            "space_name": "My Test Space",
            "invited_by": "owner123",
            "code": "secretcode",
        },
    )


@pytest.mark.asyncio
async def test_invite_user_already_member(service):
    svc, repo, _ = service
    svc._require_can = AsyncMock()
    
    space = MagicMock()
    repo.get_by_id.return_value = space
    repo.get_membership.return_value = MagicMock()  # already member
    
    with pytest.raises(AppError) as exc_info:
        await svc.invite_user(
            actor_user_id="owner123",
            space_id="space123",
            user_id="invitee123",
        )
    assert exc_info.value.code == "ALREADY_MEMBER"


@pytest.mark.asyncio
async def test_redeem_invite_guard_invitee(service):
    svc, repo, _ = service
    
    invite = MagicMock()
    invite.target_id = "space123"
    invite.invitee_id = "invitee123"
    repo.get_invite_by_code.return_value = invite
    
    # Trying to redeem with a different user_id
    with pytest.raises(AppError) as exc_info:
        await svc.redeem_invite(
            user_id="wrong_user",
            code="secretcode",
        )
    assert exc_info.value.code == "INVITE_FORBIDDEN"
    assert exc_info.value.status_code == 403


def _channel_doc(str_id: str, name: str, visibility: str) -> MagicMock:
    """A stand-in ChannelDocument. `name` is a reserved MagicMock kwarg, so it
    has to be assigned rather than passed to the constructor."""
    doc = MagicMock(
        str_id=str_id,
        slug=name.lower(),
        description=f"{name} desc",
        kind="text",
        visibility=visibility,
        posting_policy="members",
    )
    doc.name = name
    return doc


@pytest.mark.asyncio
async def test_list_channels(service):
    """Space channels come from `channels`, filtered by per-channel read access."""
    svc, repo, _ = service
    svc.check_membership = AsyncMock(return_value=True)
    # `visible` passes the authorization gate, `hidden` does not.
    svc.authorization = AsyncMock()
    svc.authorization.can = AsyncMock(side_effect=[True, False])
    svc.relationships.find_edge = AsyncMock(
        return_value=MagicMock(status="active")
    )

    with patch("app.modules.spaces.service.ChannelDocument.find") as mock_find:
        mock_query = MagicMock()
        mock_query.to_list = AsyncMock(
            return_value=[_channel_doc("c1", "Visible", "members"),
                          _channel_doc("c2", "Hidden", "private")]
        )
        mock_find.return_value = mock_query

        channels = await svc.list_channels(
            space_id="507f1f77bcf86cd799439011",
            user_id="user123",
        )

        assert [c.id for c in channels] == ["c1"]
        assert channels[0].joined is True
        # `space_id` is a StrId, so the query must use the string form.
        mock_find.assert_called_once_with({"space_id": "507f1f77bcf86cd799439011"})


@pytest.mark.asyncio
async def test_join_channel_success(service):
    svc, repo, _ = service
    svc.check_membership = AsyncMock(return_value=True)
    svc.relationships.upsert_membership = AsyncMock()

    with patch("app.modules.spaces.service.ChannelDocument.get") as mock_get:
        channel = MagicMock()
        channel.str_id = "507f1f77bcf86cd799439012"
        channel.space_id = "507f1f77bcf86cd799439011"
        channel.join_policy = "open"
        mock_get.return_value = channel

        await svc.join_channel(
            space_id="507f1f77bcf86cd799439011",
            channel_id="507f1f77bcf86cd799439012",
            user_id="user123",
        )

        svc.relationships.upsert_membership.assert_called_once()
        kwargs = svc.relationships.upsert_membership.call_args.kwargs
        assert kwargs["target_type"] == "channel"
        assert kwargs["user_id"] == "user123"


@pytest.mark.asyncio
async def test_join_channel_rejects_non_open_policy(service):
    svc, repo, _ = service
    svc.check_membership = AsyncMock(return_value=True)

    with patch("app.modules.spaces.service.ChannelDocument.get") as mock_get:
        channel = MagicMock()
        channel.str_id = "507f1f77bcf86cd799439012"
        channel.space_id = "507f1f77bcf86cd799439011"
        channel.join_policy = "invite_only"
        mock_get.return_value = channel

        with pytest.raises(AppError) as exc_info:
            await svc.join_channel(
                space_id="507f1f77bcf86cd799439011",
                channel_id="507f1f77bcf86cd799439012",
                user_id="user123",
            )
        assert exc_info.value.code == "CHANNEL_JOIN_FORBIDDEN"
        assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_join_channel_rejects_channel_outside_space(service):
    svc, repo, _ = service
    svc.check_membership = AsyncMock(return_value=True)

    with patch("app.modules.spaces.service.ChannelDocument.get") as mock_get:
        channel = MagicMock()
        channel.str_id = "507f1f77bcf86cd799439012"
        channel.space_id = "507f1f77bcf86cd799439099"
        channel.join_policy = "open"
        mock_get.return_value = channel

        with pytest.raises(AppError) as exc_info:
            await svc.join_channel(
                space_id="507f1f77bcf86cd799439011",
                channel_id="507f1f77bcf86cd799439012",
                user_id="user123",
            )
        assert exc_info.value.code == "CHANNEL_NOT_FOUND"


@pytest.mark.asyncio

async def test_update_space_success(service):
    svc, repo, _ = service
    svc._require_can = AsyncMock()
    
    updated_space = MagicMock()
    updated_space.str_id = "space123"
    updated_space.name = "New Name"
    updated_space.slug = "new-slug"
    updated_space.kind = "workspace"
    updated_space.owner_user_id = "owner123"
    updated_space.created_by = "owner123"
    updated_space.avatar = None
    updated_space.visibility = "public"
    updated_space.join_policy = "open"
    updated_space.settings = {}
    updated_space.created_at = datetime.now(UTC)
    updated_space.updated_at = datetime.now(UTC)
    
    repo.update_space.return_value = updated_space
    
    membership = MagicMock()
    membership.role = "owner"
    repo.get_membership.return_value = membership
    
    with patch("app.db.models.AuditLogDocument.insert", new_callable=AsyncMock) as mock_audit:
        res = await svc.update_space(
            actor_user_id="owner123",
            space_id="space123",
            name="New Name",
            visibility="public",
        )
        assert res.name == "New Name"
        assert res.visibility == "public"
        assert res.viewer_role == "owner"
        mock_audit.assert_called_once()


@pytest.mark.asyncio
async def test_redeem_invite_success_joined(service):
    svc, repo, _ = service
    
    invite = MagicMock()
    invite.target_id = "space123"
    invite.invitee_id = "user123"
    invite.approval_required = False
    invite.role_ids = []
    invite.code = "secretcode"
    
    repo.get_invite_by_code.return_value = invite
    repo.get_membership.return_value = None  # not a member yet
    
    space = MagicMock()
    space.str_id = "space123"
    space.name = "Test Space"
    space.slug = "test-space"
    space.kind = "workspace"
    space.owner_user_id = "owner123"
    space.created_by = "owner123"
    space.avatar = None
    space.visibility = "public"
    space.join_policy = "open"
    space.settings = {}
    space.created_at = datetime.now(UTC)
    space.updated_at = datetime.now(UTC)
    repo.get_by_id.return_value = space
    repo.consume_invite_use.return_value = invite
    repo.ensure_membership = AsyncMock()
    membership = MagicMock()
    membership.status = "active"
    svc.relationships.find_edge.side_effect = [None, membership]
    
    status, space_view, returned_membership = await svc.redeem_invite(
        user_id="user123",
        code="secretcode",
    )
    
    assert status == "joined"
    assert space_view.name == "Test Space"
    assert returned_membership is membership
    repo.consume_invite_use.assert_called_once_with(code="secretcode")
    repo.ensure_membership.assert_called_once_with(
        space_id="space123",
        user_id="user123",
        role=ROLE_MEMBER,
    )


@pytest.mark.asyncio
async def test_redeem_invite_success_pending(service):
    svc, repo, _ = service
    
    invite = MagicMock()
    invite.target_id = "space123"
    invite.invitee_id = "user123"
    invite.approval_required = True
    invite.role_ids = []
    invite.code = "secretcode"
    
    repo.get_invite_by_code.return_value = invite
    svc.relationships.find_edge.return_value = None
    
    space = MagicMock()
    space.str_id = "space123"
    space.visibility = "public"
    repo.get_by_id.return_value = space
    
    repo.consume_invite_use.return_value = invite
    membership = MagicMock()
    membership.str_id = "membership123"
    membership.status = "pending"

    with patch(
        "app.modules.spaces.service.RelationshipService.request",
        new=AsyncMock(return_value=membership),
    ) as request:
        status, space_view, returned_membership = await svc.redeem_invite(
            user_id="user123",
            code="secretcode",
        )
    
    assert status == "pending"
    assert space_view is None
    assert returned_membership is membership
    request.assert_awaited_once_with(
        kind="membership",
        user_id="user123",
        target_type="space",
        target_id="space123",
        status="pending",
        role_ids=[],
    )


@pytest.mark.asyncio
async def test_list_members_success(service):
    svc, repo, _ = service
    svc.check_membership = AsyncMock(return_value=True)
    
    member_doc = MagicMock()
    member_doc.str_id = "mem123"
    member_doc.space_id = "space123"
    member_doc.user_id = "user123"
    member_doc.role = "member"
    member_doc.joined_at = datetime.now(UTC)
    
    repo.list_members_for_space.return_value = [member_doc]
    
    user_doc = MagicMock()
    user_doc.username = "testuser"
    user_doc.display_name = "Test User"
    user_doc.avatar = None
    
    with patch("app.modules.auth.repository.UsersRepository.find_by_ids", new_callable=AsyncMock) as mock_find_users:
        mock_find_users.return_value = {"user123": user_doc}
        res = await svc.list_members(space_id="space123", user_id="user123")
        assert len(res) == 1
        assert res[0].id == "mem123"
        assert res[0].user.username == "testuser"
        mock_find_users.assert_called_once_with(["user123"])


@pytest.mark.asyncio
async def test_get_space_success(service):
    svc, repo, _ = service
    
    space = MagicMock()
    space.str_id = "space123"
    space.name = "My Test Space"
    space.slug = "my-test-space"
    space.kind = "workspace"
    space.owner_user_id = "owner123"
    space.created_by = "owner123"
    space.avatar = None
    space.visibility = "public"
    space.join_policy = "open"
    space.settings = {}
    space.created_at = datetime.now(UTC)
    space.updated_at = datetime.now(UTC)
    repo.get_by_id.return_value = space
    
    membership = MagicMock()
    membership.role = "admin"
    repo.get_membership.return_value = membership
    
    res = await svc.get_space(space_id="space123", user_id="user123")
    assert res.id == "space123"
    assert res.viewer_role == "admin"
    repo.get_membership.assert_called_once_with(space_id="space123", user_id="user123")


@pytest.mark.asyncio
async def test_list_spaces_success(service):
    svc, repo, _ = service
    
    space = MagicMock()
    space.str_id = "space123"
    space.name = "My Test Space"
    space.slug = "my-test-space"
    space.kind = "workspace"
    space.owner_user_id = "owner123"
    space.created_by = "owner123"
    space.avatar = None
    space.visibility = "public"
    space.join_policy = "open"
    space.settings = {}
    space.created_at = datetime.now(UTC)
    space.updated_at = datetime.now(UTC)
    
    membership = MagicMock()
    membership.space_id = "space123"
    membership.role = "member"
    repo.list_memberships_for_user.return_value = [membership]
    repo.get_by_id.return_value = space
    
    res = await svc.list_for_user(user_id="user123")
    assert len(res) == 1
    assert res[0].id == "space123"
    assert res[0].viewer_role == "member"
    repo.list_memberships_for_user.assert_called_once_with(user_id="user123")
    repo.get_by_id.assert_called_once_with("space123")
