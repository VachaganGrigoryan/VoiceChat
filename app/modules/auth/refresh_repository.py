from __future__ import annotations

from datetime import datetime, UTC
from typing import Any, Optional

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.errors import AppError
from app.db.indexes import COL_REFRESH_TOKENS


def _oid(s: str) -> ObjectId:
    try:
        return ObjectId(s)
    except Exception:
        raise AppError(code="INVALID_ID", message="Invalid id", status_code=400)


class RefreshTokensRepository:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.col = db[COL_REFRESH_TOKENS]

    async def create_token(
        self,
        *,
        user_id: str,
        token_hash: str,
        expires_at: datetime,
        user_agent: str | None = None,
        ip: str | None = None,
    ) -> dict[str, Any]:
        now = datetime.now(UTC)
        doc = {
            "user_id": _oid(user_id),
            "token_hash": token_hash,
            "expires_at": expires_at,
            "revoked_at": None,
            "replaced_by_token_hash": None,
            "created_at": now,
            "updated_at": now,
            "user_agent": user_agent,
            "ip": ip,
        }
        res = await self.col.insert_one(doc)
        doc["_id"] = res.inserted_id
        return doc

    async def find_active_by_hash(self, *, token_hash: str) -> Optional[dict[str, Any]]:
        now = datetime.now(UTC)
        return await self.col.find_one(
            {
                "token_hash": token_hash,
                "revoked_at": None,
                "expires_at": {"$gt": now},
            }
        )

    async def find_any_by_hash(self, *, token_hash: str) -> Optional[dict[str, Any]]:
        return await self.col.find_one({"token_hash": token_hash})

    async def revoke_token(
        self,
        *,
        token_hash: str,
        replaced_by_token_hash: str | None = None,
    ) -> None:
        now = datetime.now(UTC)
        await self.col.update_one(
            {"token_hash": token_hash},
            {
                "$set": {
                    "revoked_at": now,
                    "updated_at": now,
                    "replaced_by_token_hash": replaced_by_token_hash,
                }
            },
        )

    async def revoke_all_for_user(self, *, user_id: str) -> None:
        now = datetime.now(UTC)
        await self.col.update_many(
            {
                "user_id": _oid(user_id),
                "revoked_at": None,
            },
            {"$set": {"revoked_at": now, "updated_at": now}},
        )