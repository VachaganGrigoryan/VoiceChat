from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from app.core.errors import AppError
from app.modules.spaces.service import SpacesService


@pytest.fixture
def service():
    repo = AsyncMock()
    notifications_service = AsyncMock()
    
    # Mock require_manager / check_membership
    repo.get_membership = AsyncMock()
    
    svc = SpacesService(
        repo=repo,
        notifications_service=notifications_service,
    )
    return svc, repo, notifications_service


@pytest.mark.asyncio
async def test_invite_user_success(service):
    svc, repo, notifications = service
    
    # Mock require_manager and get_by_id checks
    svc.require_manager = AsyncMock()
    
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
    invite_doc.use_count = 0
    invite_doc.requires_approval = False
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
        kind="space_invite",
        source_type="space",
        source_id="space123",
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
    svc.require_manager = AsyncMock()
    
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


@pytest.mark.asyncio
async def test_list_channels(service):
    svc, repo, _ = service
    svc.check_membership = AsyncMock(return_value=True)
    
    with patch("app.db.models.ConversationDocument.find") as mock_find:
        mock_query = MagicMock()
        mock_query.to_list = AsyncMock(return_value=[
            MagicMock(
                str_id="c1",
                title="Channel 1",
                description="Desc 1",
                space_visibility="space_public",
                participant_ids=["user123"]
            ),
            MagicMock(
                str_id="c2",
                title="Channel 2",
                description="Desc 2",
                space_visibility="invite_only",
                participant_ids=["other_user"]
            ),
        ])
        mock_find.return_value = mock_query
        
        channels = await svc.list_channels(
            space_id="507f1f77bcf86cd799439011",
            user_id="user123",
        )
        
        assert len(channels) == 2
        assert channels[0].id == "c1"
        assert channels[0].joined is True
        assert channels[1].id == "c2"
        assert channels[1].joined is False

        from app.db.object_id import parse_object_id
        mock_find.assert_called_once_with({
            "space_id": parse_object_id("507f1f77bcf86cd799439011"),
            "type": "channel",
            "$or": [
                {"space_visibility": "space_public"},
                {"participant_ids": "user123"}
            ]
        })


@pytest.mark.asyncio
async def test_join_channel_success(service):
    svc, repo, _ = service
    svc.check_membership = AsyncMock(return_value=True)
    
    with patch("app.db.models.ConversationDocument.get") as mock_get:
        conv = MagicMock()
        conv.type = "channel"
        conv.space_id = "space123"
        from app.db.object_id import parse_object_id
        conv.space_id = parse_object_id("507f1f77bcf86cd799439011")
        conv.space_visibility = "space_public"
        mock_get.return_value = conv
        
        with patch("app.modules.conversations.repository.ConversationsRepository") as mock_conv_repo_cls:
            mock_conv_repo = MagicMock()
            mock_conv_repo.ensure_participant = AsyncMock()
            mock_conv_repo.add_participant_id = AsyncMock()
            mock_conv_repo_cls.return_value = mock_conv_repo
            
            await svc.join_channel(
                space_id="507f1f77bcf86cd799439011",
                conversation_id="507f1f77bcf86cd799439012",
                user_id="user123",
            )
            
            mock_conv_repo.ensure_participant.assert_called_once()
            mock_conv_repo.add_participant_id.assert_called_once()


@pytest.mark.asyncio
async def test_join_channel_reject_invite_only(service):
    svc, repo, _ = service
    svc.check_membership = AsyncMock(return_value=True)
    
    with patch("app.db.models.ConversationDocument.get") as mock_get:
        conv = MagicMock()
        conv.type = "channel"
        from app.db.object_id import parse_object_id
        conv.space_id = parse_object_id("507f1f77bcf86cd799439011")
        conv.space_visibility = "invite_only"
        mock_get.return_value = conv
        
        with pytest.raises(AppError) as exc_info:
            await svc.join_channel(
                space_id="507f1f77bcf86cd799439011",
                conversation_id="507f1f77bcf86cd799439012",
                user_id="user123",
            )
        assert exc_info.value.code == "CHANNEL_JOIN_FORBIDDEN"
        assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_update_space_success(service):
    svc, repo, _ = service
    svc.require_manager = AsyncMock()
    
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
    invite.requires_approval = False
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
    
    status, space_view, req_view = await svc.redeem_invite(
        user_id="user123",
        code="secretcode",
    )
    
    assert status == "joined"
    assert space_view.name == "Test Space"
    assert req_view is None
    repo.consume_invite_use.assert_called_once_with(code="secretcode")
    repo.ensure_membership.assert_called_once_with(
        space_id="space123",
        user_id="user123",
        role="member",
    )


@pytest.mark.asyncio
async def test_redeem_invite_success_pending(service):
    svc, repo, _ = service
    
    invite = MagicMock()
    invite.target_id = "space123"
    invite.invitee_id = "user123"
    invite.requires_approval = True
    invite.code = "secretcode"
    
    repo.get_invite_by_code.return_value = invite
    repo.get_membership.return_value = None
    
    space = MagicMock()
    space.str_id = "space123"
    space.visibility = "public"
    repo.get_by_id.return_value = space
    
    repo.get_pending_join_request.return_value = None
    
    req_doc = MagicMock()
    req_doc.str_id = "req123"
    req_doc.space_id = "space123"
    req_doc.user_id = "user123"
    req_doc.status = "pending"
    req_doc.invite_code = "secretcode"
    req_doc.created_at = datetime.now(UTC)
    repo.create_join_request.return_value = req_doc
    
    status, space_view, req_view = await svc.redeem_invite(
        user_id="user123",
        code="secretcode",
    )
    
    assert status == "pending"
    assert space_view is None
    assert req_view.id == "req123"
    assert req_view.status == "pending"
    repo.create_join_request.assert_called_once_with(
        space_id="space123",
        user_id="user123",
        invite_code="secretcode",
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
