from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.core.errors import AppError
from app.db.models import BlockDocument, RelationshipDocument, UserDocument
from app.modules.blocks.repository import BlocksRepository
from app.modules.blocks.schemas import BlockedUserListItem, to_block_view
from app.modules.relationships.repository import RelationshipsRepository
from app.modules.relationships.schemas import PeerUserSummary
from app.modules.relationships.service import RelationshipService
from app.modules.users.avatar import build_user_avatar_payload


class UsersRepositoryProto(Protocol):
    async def find_by_id(self, user_id: str) -> UserDocument | None: ...


class PresenceServiceProto(Protocol):
    async def is_online(self, user_id: str) -> bool: ...


@dataclass(frozen=True)
class BlockResult:
    block: BlockDocument
    revoked_relationship: RelationshipDocument | None


class BlocksService:
    def __init__(
        self,
        *,
        repo: BlocksRepository,
        relationships: RelationshipsRepository,
        relationship_service: RelationshipService,
        users_repo: UsersRepositoryProto,
        presence_service: PresenceServiceProto | None = None,
    ) -> None:
        self.repo = repo
        self.relationships = relationships
        self.relationship_service = relationship_service
        self.users_repo = users_repo
        self.presence_service = presence_service

    async def block(self, *, blocker_id: str, blocked_id: str) -> BlockResult:
        if str(blocker_id) == str(blocked_id):
            raise AppError(
                code="INVALID_BLOCK_TARGET",
                message="Cannot block yourself",
                status_code=400,
            )
        if await self.users_repo.find_by_id(blocked_id) is None:
            raise AppError(
                code="USER_NOT_FOUND",
                message="User not found",
                status_code=404,
            )

        block = await self.repo.create(
            blocker_id=blocker_id,
            blocked_id=blocked_id,
        )
        relationship = await self.relationships.find_connection(
            user_a=blocker_id,
            user_b=blocked_id,
        )
        revoked_relationship = None
        if relationship is not None and relationship.status != "revoked":
            revoked_relationship = await self.relationship_service.revoke(
                relationship_id=relationship.str_id
            )
        return BlockResult(
            block=block,
            revoked_relationship=revoked_relationship,
        )

    async def unblock(self, *, blocker_id: str, blocked_id: str) -> BlockDocument:
        doc = await self.repo.delete(
            blocker_id=blocker_id,
            blocked_id=blocked_id,
        )
        if doc is None:
            raise AppError(
                code="BLOCK_NOT_FOUND",
                message="User is not blocked",
                status_code=404,
            )
        return doc

    async def list_blocked_users(
        self,
        *,
        blocker_id: str,
        limit: int,
        cursor: str | None,
    ) -> tuple[list[BlockedUserListItem], str | None]:
        docs, next_cursor = await self.repo.list_page(
            blocker_id=blocker_id,
            limit=limit,
            cursor=cursor,
        )
        items = [await self._to_list_item(doc) for doc in docs]
        return items, next_cursor

    async def _to_list_item(self, doc: BlockDocument) -> BlockedUserListItem:
        user_id = str(doc.blocked_id)
        user = await self.users_repo.find_by_id(user_id)
        online = (
            await self.presence_service.is_online(user_id)
            if self.presence_service is not None
            else False
        )
        return BlockedUserListItem(
            block=to_block_view(doc),
            user=PeerUserSummary(
                id=user_id,
                username=str(getattr(user, "username", "") or ""),
                display_name=getattr(user, "display_name", None),
                avatar=(
                    build_user_avatar_payload(getattr(user, "avatar", None))
                    if user is not None
                    else None
                ),
                is_online=online,
            ),
        )
