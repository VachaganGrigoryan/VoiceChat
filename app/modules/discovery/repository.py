from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from beanie.operators import Eq, Inc, Set

from app.db.models import DiscoveryTokenDocument
from app.db.object_id import parse_object_id as _oid
from app.db.repository import BaseRepository


class DiscoveryTokensRepository(BaseRepository[DiscoveryTokenDocument]):
    model = DiscoveryTokenDocument

    async def deactivate_active_codes_for_user(self, *, user_id: str) -> None:
        await DiscoveryTokenDocument.find(
            DiscoveryTokenDocument.user_id == user_id,
            DiscoveryTokenDocument.type == "code",
            Eq(DiscoveryTokenDocument.is_active, True),
        ).update(
            Set(
                {
                    DiscoveryTokenDocument.is_active: False,
                    DiscoveryTokenDocument.updated_at: datetime.now(UTC),
                }
            )
        )

    async def create_token(self, doc: dict[str, Any]) -> DiscoveryTokenDocument:
        token = DiscoveryTokenDocument(**doc)
        await token.insert()
        return token

    async def find_active_by_hash(
        self, *, token_hash: str, token_type: str
    ) -> DiscoveryTokenDocument | None:
        return await DiscoveryTokenDocument.find_one(
            DiscoveryTokenDocument.token_hash == token_hash,
            DiscoveryTokenDocument.type == token_type,
            Eq(DiscoveryTokenDocument.is_active, True),
        )

    async def increment_use(self, *, token_id: str, now: datetime) -> None:
        await DiscoveryTokenDocument.find(
            DiscoveryTokenDocument.id == _oid(token_id)
        ).update(
            Inc({DiscoveryTokenDocument.use_count: 1}),
            Set(
                {
                    DiscoveryTokenDocument.used_at: now,
                    DiscoveryTokenDocument.updated_at: now,
                }
            ),
        )

    async def list_by_user_id(
        self, *, user_id: str, token_type: str | None = None
    ) -> list[DiscoveryTokenDocument]:
        conditions: list[Any] = [DiscoveryTokenDocument.user_id == user_id]
        if token_type:
            conditions.append(DiscoveryTokenDocument.type == token_type)
        return (
            await DiscoveryTokenDocument.find(*conditions)
            .sort("-created_at")
            .limit(100)
            .to_list()
        )

    async def deactivate_token(self, *, token_id: str) -> None:
        await DiscoveryTokenDocument.find(
            DiscoveryTokenDocument.id == _oid(token_id)
        ).update(
            Set(
                {
                    DiscoveryTokenDocument.is_active: False,
                    DiscoveryTokenDocument.updated_at: datetime.now(UTC),
                }
            )
        )
