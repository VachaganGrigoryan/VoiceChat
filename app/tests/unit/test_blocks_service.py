from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from app.core.errors import AppError
from app.db.models import BlockDocument, RelationshipDocument
from app.modules.blocks.service import BlocksService


def make_block() -> BlockDocument:
    now = datetime.now(UTC)
    return BlockDocument(
        blocker_id="user-a",
        blocked_id="user-b",
        created_at=now,
        updated_at=now,
    )


def make_connection(status: str = "active") -> RelationshipDocument:
    now = datetime.now(UTC)
    return RelationshipDocument(
        kind="connection",
        user_id="user-a",
        target_type="user",
        target_id="user-b",
        status=status,
        initiation="request",
        initiated_by="user-a",
        pair_id="user-a_user-b",
        requested_at=now,
        created_at=now,
        updated_at=now,
    )


@pytest.fixture
def service():
    blocks = AsyncMock()
    relationships = AsyncMock()
    relationship_service = AsyncMock()
    users = AsyncMock()
    users.find_by_id.return_value = {"_id": "user-b"}
    return (
        BlocksService(
            repo=blocks,
            relationships=relationships,
            relationship_service=relationship_service,
            users_repo=users,
        ),
        blocks,
        relationships,
        relationship_service,
    )


@pytest.mark.asyncio
async def test_block_revokes_existing_connection(service):
    svc, blocks, relationships, relationship_service = service
    block = make_block()
    connection = make_connection()
    revoked = make_connection(status="revoked")
    blocks.create.return_value = block
    relationships.find_connection.return_value = connection
    relationship_service.revoke.return_value = revoked

    result = await svc.block(blocker_id="user-a", blocked_id="user-b")

    assert result.block is block
    assert result.revoked_relationship is revoked
    relationship_service.revoke.assert_awaited_once()


@pytest.mark.asyncio
async def test_unblock_does_not_restore_connection(service):
    svc, blocks, relationships, relationship_service = service
    blocks.delete.return_value = make_block()

    await svc.unblock(blocker_id="user-a", blocked_id="user-b")

    relationships.find_connection.assert_not_awaited()
    relationship_service.activate.assert_not_awaited()


@pytest.mark.asyncio
async def test_unblock_missing_block_returns_not_found(service):
    svc, blocks, _, _ = service
    blocks.delete.return_value = None

    with pytest.raises(AppError) as exc:
        await svc.unblock(blocker_id="user-a", blocked_id="user-b")

    assert exc.value.code == "BLOCK_NOT_FOUND"
