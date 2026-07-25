from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from app.core.errors import AppError
from app.modules.conversations.service import ConversationsService


@pytest.fixture
def service():
    repo = AsyncMock()
    pings_service = AsyncMock()
    users_repo = AsyncMock()
    presence_service = AsyncMock()
    
    svc = ConversationsService(
        repo=repo,
        pings_service=pings_service,
        users_repo=users_repo,
        presence_service=presence_service,
    )
    return svc, repo, pings_service


@pytest.mark.asyncio
async def test_create_group_conversation_bypasses_ping_with_space_id(service):
    svc, repo, pings_service = service
    
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
        pings_service.ensure_can_message.assert_not_called()
        repo.create_group.assert_called_once()


@pytest.mark.asyncio
async def test_create_group_conversation_enforces_ping_without_space_id(service):
    svc, repo, pings_service = service
    
    # Mock ensure_can_message to raise error (simulating no accepted ping)
    pings_service.ensure_can_message.side_effect = AppError(
        code="PING_REQUIRED", message="Ping required", status_code=403
    )
    
    with pytest.raises(AppError) as exc_info:
        await svc.create_group_conversation(
            user_id="user1",
            title="Group Title",
            participant_ids=["user2"],
            space_id=None,
        )
    
    assert exc_info.value.code == "PING_REQUIRED"
    pings_service.ensure_can_message.assert_called_once_with(
        sender_id="user1", receiver_id="user2"
    )


@pytest.mark.asyncio
async def test_add_group_members_bypasses_ping_for_space_members(service):
    svc, repo, pings_service = service
    
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
    ) as mock_find_member:
        # User is in space
        mock_find_member.return_value = MagicMock()
        
        await svc.add_group_members(
            actor_user_id="owner1",
            conversation_id="conv123",
            participant_ids=["user2"],
        )
        
        # ensure_can_message should be bypassed
        pings_service.ensure_can_message.assert_not_called()
        repo.ensure_participant.assert_called_once_with(
            conversation_id="conv123",
            user_id="user2",
            role="member",
        )


@pytest.mark.asyncio
async def test_add_group_members_enforces_ping_for_non_space_members(service):
    svc, repo, pings_service = service
    
    svc._require_actor_role = AsyncMock()
    
    conversation = MagicMock()
    conversation.str_id = "conv123"
    conversation.space_id = "space123"
    conversation.type = "group"
    repo.get_for_participant.return_value = conversation
    
    pings_service.ensure_can_message.side_effect = AppError(
        code="PING_REQUIRED", message="Ping required", status_code=403
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
        
        assert exc_info.value.code == "PING_REQUIRED"
        pings_service.ensure_can_message.assert_called_once_with(
            sender_id="owner1", receiver_id="user2"
        )
