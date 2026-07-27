from __future__ import annotations

from app.modules.auth.repository import UsersRepository
from app.modules.blocks.repository import BlocksRepository
from app.modules.blocks.service import BlocksService
from app.modules.relationships.repository import RelationshipsRepository
from app.modules.relationships.service import RelationshipService
from app.modules.realtime.presence import get_presence_backend


def get_blocks_service() -> BlocksService:
    relationships = RelationshipsRepository()
    return BlocksService(
        repo=BlocksRepository(),
        relationships=relationships,
        relationship_service=RelationshipService(repo=relationships),
        users_repo=UsersRepository(),
        presence_service=get_presence_backend(),
    )
