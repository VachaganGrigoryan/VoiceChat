from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import DESCENDING

from app.core.errors import AppError
from app.db.indexes import COL_DISCOVERY_TOKENS


def _oid(value: str) -> ObjectId:
    try:
        return ObjectId(value)
    except Exception as exc:
        raise AppError(code="INVALID_ID", message="Invalid id", status_code=400) from exc


class DiscoveryTokensRepository:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.col = db[COL_DISCOVERY_TOKENS]

    async def deactivate_active_codes_for_user(self, *, user_id: str) -> None:
        await self.col.update_many(
            {
                "user_id": user_id,
                "type": "code",
                "is_active": True,
            },
            {
                "$set": {
                    "is_active": False,
                    "updated_at": datetime.now(UTC),
                }
            },
        )

    async def create_token(self, doc: dict[str, Any]) -> dict[str, Any]:
        result = await self.col.insert_one(doc)
        doc["_id"] = result.inserted_id
        return doc

    async def find_active_by_hash(self, *, token_hash: str, token_type: str) -> dict[str, Any] | None:
        return await self.col.find_one(
            {
                "token_hash": token_hash,
                "type": token_type,
                "is_active": True,
            }
        )

    async def increment_use(self, *, token_id: str, now: datetime) -> None:
        await self.col.update_one(
            {"_id": _oid(token_id)},
            {
                "$inc": {"use_count": 1},
                "$set": {"used_at": now, "updated_at": now},
            },
        )

    async def list_by_user_id(self, *, user_id: str, token_type: str | None = None) -> list[dict[str, Any]]:
        query: dict[str, Any] = {"user_id": user_id}
        if token_type:
            query["type"] = token_type
        cursor = self.col.find(query).sort("created_at", DESCENDING)
        return await cursor.to_list(length=100)

    async def deactivate_token(self, *, token_id: str) -> None:
        await self.col.update_one(
            {"_id": _oid(token_id)},
            {"$set": {"is_active": False, "updated_at": datetime.now(UTC)}},
        )