from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.errors import AppError
from app.db.models import ConversationDocument, MessageDocument
from app.modules.messages.dependencies import MessageContext, require_message_access


@pytest.mark.asyncio
async def test_resolver_unknown_message_404():
    messages_repo = AsyncMock()
    messages_repo.get_by_id.return_value = None

    authz = AsyncMock()
    conversations_repo = AsyncMock()

    with pytest.raises(AppError) as exc_info:
        await require_message_access(
            message_id="msg_missing",
            user=MagicMock(str_id="usr_123"),
            messages_repo=messages_repo,
            conversations_repo=conversations_repo,
            authz=authz,
        )

    assert exc_info.value.status_code == 404
    assert exc_info.value.code == "MESSAGE_NOT_FOUND"


@pytest.mark.asyncio
async def test_resolver_unpermitted_container_403():
    message = MagicMock(spec=MessageDocument)
    message.container_type = "conversation"
    message.container_id = "conv_123"

    messages_repo = AsyncMock()
    messages_repo.get_by_id.return_value = message

    authz = AsyncMock()
    authz.require.side_effect = AppError(
        code="FORBIDDEN", message="Not allowed to read", status_code=403
    )

    conversations_repo = AsyncMock()

    with pytest.raises(AppError) as exc_info:
        await require_message_access(
            message_id="msg_secret",
            user=MagicMock(str_id="usr_unauth"),
            messages_repo=messages_repo,
            conversations_repo=conversations_repo,
            authz=authz,
        )

    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_resolver_conversation_participant_success():
    message = MagicMock(spec=MessageDocument)
    message.container_type = "conversation"
    message.container_id = "conv_123"

    messages_repo = AsyncMock()
    messages_repo.get_by_id.return_value = message

    conversation = MagicMock(spec=ConversationDocument)
    conversation.str_id = "conv_123"
    conversation.participant_ids = ["usr_123", "usr_456"]

    conversations_repo = AsyncMock()
    conversations_repo.get_by_id.return_value = conversation

    authz = AsyncMock()
    authz.require.return_value = None

    ctx = await require_message_access(
        message_id="msg_ok",
        user=MagicMock(str_id="usr_123"),
        messages_repo=messages_repo,
        conversations_repo=conversations_repo,
        authz=authz,
    )

    assert isinstance(ctx, MessageContext)
    assert ctx.container_type == "conversation"
    assert ctx.container_id == "conv_123"
    assert ctx.conversation == conversation
    assert ctx.require_conversation_container() == conversation


@pytest.mark.asyncio
async def test_resolver_channel_reader_success():
    message = MagicMock(spec=MessageDocument)
    message.container_type = "channel"
    message.container_id = "chan_789"

    messages_repo = AsyncMock()
    messages_repo.get_by_id.return_value = message

    conversations_repo = AsyncMock()
    authz = AsyncMock()
    authz.require.return_value = None

    ctx = await require_message_access(
        message_id="msg_chan",
        user=MagicMock(str_id="usr_reader"),
        messages_repo=messages_repo,
        conversations_repo=conversations_repo,
        authz=authz,
    )

    assert ctx.container_type == "channel"
    assert ctx.container_id == "chan_789"
    assert ctx.conversation is None

    with pytest.raises(AppError) as exc_info:
        ctx.require_conversation_container()
    assert exc_info.value.status_code == 400
    assert exc_info.value.code == "INVALID_CONTAINER"
