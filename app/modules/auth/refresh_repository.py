from __future__ import annotations

from datetime import UTC, datetime

from beanie.operators import Eq, Set

from app.db.models import RefreshTokenDocument
from app.db.repository import BaseRepository


class RefreshTokensRepository(BaseRepository[RefreshTokenDocument]):
    model = RefreshTokenDocument

    async def create_token(
        self,
        *,
        user_id: str,
        token_hash: str,
        expires_at: datetime,
        user_agent: str | None = None,
        ip: str | None = None,
    ) -> RefreshTokenDocument:
        now = datetime.now(UTC)
        doc = RefreshTokenDocument(
            user_id=user_id,
            token_hash=token_hash,
            expires_at=expires_at,
            created_at=now,
            updated_at=now,
            user_agent=user_agent,
            ip=ip,
        )
        await doc.insert()
        return doc

    async def find_active_by_hash(
        self, *, token_hash: str
    ) -> RefreshTokenDocument | None:
        now = datetime.now(UTC)
        return await RefreshTokenDocument.find_one(
            RefreshTokenDocument.token_hash == token_hash,
            Eq(RefreshTokenDocument.revoked_at, None),
            RefreshTokenDocument.expires_at > now,
        )

    async def find_any_by_hash(self, *, token_hash: str) -> RefreshTokenDocument | None:
        return await RefreshTokenDocument.find_one(
            RefreshTokenDocument.token_hash == token_hash
        )

    async def revoke_token(
        self,
        *,
        token_hash: str,
        replaced_by_token_hash: str | None = None,
    ) -> None:
        now = datetime.now(UTC)
        await RefreshTokenDocument.find(
            RefreshTokenDocument.token_hash == token_hash
        ).update(
            Set(
                {
                    RefreshTokenDocument.revoked_at: now,
                    RefreshTokenDocument.updated_at: now,
                    RefreshTokenDocument.replaced_by_token_hash: replaced_by_token_hash,
                }
            )
        )

    async def revoke_all_for_user(self, *, user_id: str) -> None:
        now = datetime.now(UTC)
        await RefreshTokenDocument.find(
            RefreshTokenDocument.user_id == user_id,
            Eq(RefreshTokenDocument.revoked_at, None),
        ).update(
            Set(
                {
                    RefreshTokenDocument.revoked_at: now,
                    RefreshTokenDocument.updated_at: now,
                }
            )
        )
