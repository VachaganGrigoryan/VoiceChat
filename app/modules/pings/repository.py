from __future__ import annotations

from datetime import datetime, UTC
from typing import Any

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import DESCENDING
from pymongo.errors import DuplicateKeyError

from app.core.errors import AppError

COL_PINGS = "pings"


def _oid(value: str) -> ObjectId:
    try:
        return ObjectId(value)
    except Exception as exc:
        raise AppError(code="INVALID_ID", message="Invalid id", status_code=400) from exc


def pair_id_for(user_a: str, user_b: str) -> str:
    a = str(user_a)
    b = str(user_b)
    return f"{a}_{b}" if a < b else f"{b}_{a}"


class PingsRepository:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.col = db[COL_PINGS]

    async def create_ping(self, *, from_user_id: str, to_user_id: str, status: str = "pending") -> dict[str, Any]:
        now = datetime.now(UTC)
        doc = {
            "pair_id": pair_id_for(from_user_id, to_user_id),
            "from_user_id": from_user_id,
            "to_user_id": to_user_id,
            "status": status,
            "created_at": now,
            "updated_at": now,
            "responded_at": None,
        }
        try:
            result = await self.col.insert_one(doc)
        except DuplicateKeyError as exc:
            raise AppError(code="PING_ALREADY_EXISTS", message="Ping already exists", status_code=409) from exc
        doc["_id"] = result.inserted_id
        return doc

    async def find_by_id(self, ping_id: str) -> dict[str, Any] | None:
        return await self.col.find_one({"_id": _oid(ping_id)})

    async def find_by_pair_id(self, pair_id: str) -> dict[str, Any] | None:
        return await self.col.find_one({"pair_id": pair_id})

    async def update_status(self, *, ping_id: str, status: str) -> dict[str, Any] | None:
        now = datetime.now(UTC)
        responded_at = now if status in {"accepted", "declined", "cancelled", "expired", "blocked"} else None

        await self.col.update_one(
            {"_id": _oid(ping_id)},
            {
                "$set": {
                    "status": status,
                    "updated_at": now,
                    "responded_at": responded_at,
                }
            },
        )
        return await self.find_by_id(ping_id)

    async def list_incoming(self, *, user_id: str, limit: int = 20) -> list[dict[str, Any]]:
        cursor = (
            self.col.find({"to_user_id": user_id})
            .sort("updated_at", DESCENDING)
            .limit(limit)
        )
        return await cursor.to_list(length=limit)

    async def list_outgoing(self, *, user_id: str, limit: int = 20) -> list[dict[str, Any]]:
        cursor = (
            self.col.find({"from_user_id": user_id})
            .sort("updated_at", DESCENDING)
            .limit(limit)
        )
        return await cursor.to_list(length=limit)

    async def has_accepted_permission(self, *, user_a: str, user_b: str) -> bool:
        doc = await self.col.find_one(
            {
                "pair_id": pair_id_for(user_a, user_b),
                "status": "accepted",
            },
            {"_id": 1},
        )
        return doc is not None