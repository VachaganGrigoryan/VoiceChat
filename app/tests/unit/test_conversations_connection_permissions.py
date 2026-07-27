from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from app.core.errors import AppError
from app.modules.authorization.roles import ROLE_MEMBER
from app.modules.conversations.service import ConversationsService


@pytest.fixture
def service():
    repo = AsyncMock()
    connection_service = AsyncMock()
    users_repo = AsyncMock()
    presence_service = AsyncMock()
    
    # These tests are about the connection gate, not authorization; a stub keeps the
    # `AuthorizationService.can` step from short-circuiting them.
    svc = ConversationsService(
        repo=repo,
        connection_service=connection_service,
        users_repo=users_repo,
        presence_service=presence_service,
        authorization=AsyncMock(),
    )
    return svc, repo, connection_service


@pytest.mark.asyncio
async def test_create_group_conversation_bypasses_connection_gate_with_space_id(service):
    svc, repo, connection_service = service
    
    # Mock space membership lookup (a relationship, kind=membership)
    with patch(
        "app.modules.spaces.repository.find_active_space_membership",
        new_callable=AsyncMock,
    ) as mock_find_member:
        # create_group_conversation checks every participant's space membership
        # when space_id is set.
        mock_find_member.return_value = MagicMock()
        
        # Mock repo calls
        conv = MagicMock()
        conv.str_id = "conv123"
        repo.create_group.return_value = conv
        
        await svc.create_group_conversation(
            user_id="user1",
            title="Group Title",
            participant_ids=["user2"],
            space_id="space123",
            space_visibility="space_public",
        )
        
        # _ensure_can_message should NOT have been called
        connection_service.ensure_can_message.assert_not_called()
        repo.create_group.assert_called_once()


@pytest.mark.asyncio
async def test_create_group_conversation_enforces_connection_without_space_id(service):
    svc, repo, connection_service = service
    
    # Mock ensure_can_message to raise error (simulating no active connection)
    connection_service.ensure_can_message.side_effect = AppError(
        code="CONNECTION_REQUIRED", message="Connection required", status_code=403
    )
    
    with pytest.raises(AppError) as exc_info:
        await svc.create_group_conversation(
            user_id="user1",
            title="Group Title",
            participant_ids=["user2"],
            space_id=None,
        )
    
    assert exc_info.value.code == "CONNECTION_REQUIRED"
    connection_service.ensure_can_message.assert_called_once_with(
        sender_id="user1", receiver_id="user2"
    )


@pytest.mark.asyncio
async def test_add_group_members_bypasses_connection_for_space_members(service):
    svc, repo, connection_service = service
    
    # Mock require_actor_role
    svc._require_actor_role = AsyncMock()
    
    # Mock conversation from repo
    conversation = MagicMock()
    conversation.str_id = "conv123"
    conversation.space_id = "space123"
    conversation.type = "group"
    repo.get_for_participant.return_value = conversation
    
    with patch(
        "app.modules.spaces.repository.find_active_space_membership",
        new_callable=AsyncMock,
    ) as mock_find_member, patch(
        "app.db.models.AuditLogDocument.insert",
        new_callable=AsyncMock,
    ):
        # User is in space
        mock_find_member.return_value = MagicMock()
        
        await svc.add_group_members(
            actor_user_id="owner1",
            conversation_id="conv123",
            participant_ids=["user2"],
        )
        
        # ensure_can_message should be bypassed
        connection_service.ensure_can_message.assert_not_called()
        repo.ensure_participant.assert_called_once_with(
            conversation_id="conv123",
            user_id="user2",
            role=ROLE_MEMBER,
        )


@pytest.mark.asyncio
async def test_add_group_members_enforces_connection_for_non_space_members(service):
    svc, repo, connection_service = service
    
    svc._require_actor_role = AsyncMock()
    
    conversation = MagicMock()
    conversation.str_id = "conv123"
    conversation.space_id = "space123"
    conversation.type = "group"
    repo.get_for_participant.return_value = conversation
    
    connection_service.ensure_can_message.side_effect = AppError(
        code="CONNECTION_REQUIRED", message="Connection required", status_code=403
    )
    
    with patch(
        "app.modules.spaces.repository.find_active_space_membership",
        new_callable=AsyncMock,
    ) as mock_find_member:
        # User is NOT in space
        mock_find_member.return_value = None
        
        with pytest.raises(AppError) as exc_info:
            await svc.add_group_members(
                actor_user_id="owner1",
                conversation_id="conv123",
                participant_ids=["user2"],
            )
        
        assert exc_info.value.code == "CONNECTION_REQUIRED"
        connection_service.ensure_can_message.assert_called_once_with(
            sender_id="owner1", receiver_id="user2"
        )
