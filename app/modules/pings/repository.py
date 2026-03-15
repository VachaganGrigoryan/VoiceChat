from __future__ import annotations

from datetime import datetime, UTC
from typing import Any

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import DESCENDING
from pymongo.errors import DuplicateKeyError

from app.core.errors import AppError
from app.core.pagination.cursor import decode_cursor, encode_cursor
from app.db.indexes import COL_PINGS


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

    async def get_pair_state(self, *, user_a: str, user_b: str) -> dict[str, Any] | None:
        return await self.col.find_one({"pair_id": pair_id_for(user_a, user_b)})

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

    async def list_incoming(
            self,
            *,
            user_id: str,
            limit: int = 20,
            cursor: str | None = None,
    ) -> tuple[list[dict[str, Any]], str | None]:
        query: dict[str, Any] = {"to_user_id": user_id}

        if cursor:
            payload = decode_cursor(cursor, required_fields={"created_at", "id"})
            created_at = payload["created_at"]
            oid = _oid(payload["id"])

            query["$or"] = [
                {"created_at": {"$lt": created_at}},
                {"created_at": created_at, "_id": {"$lt": oid}},
            ]

        docs = await (
            self.col.find(query)
            .sort([("created_at", DESCENDING), ("_id", DESCENDING)])
            .limit(limit + 1)
            .to_list(length=limit + 1)
        )

        next_cursor: str | None = None
        if len(docs) > limit:
            last = docs[limit - 1]
            next_cursor = encode_cursor(
                created_at=last["created_at"],
                id=str(last["_id"]),
            )
            docs = docs[:limit]

        return docs, next_cursor

    async def list_outgoing(
            self,
            *,
            user_id: str,
            limit: int = 20,
            cursor: str | None = None,
    ) -> tuple[list[dict[str, Any]], str | None]:
        query: dict[str, Any] = {"from_user_id": user_id}

        if cursor:
            payload = decode_cursor(cursor, required_fields={"created_at", "id"})
            created_at = payload["created_at"]
            oid = _oid(payload["id"])

            query["$or"] = [
                {"created_at": {"$lt": created_at}},
                {"created_at": created_at, "_id": {"$lt": oid}},
            ]

        docs = await (
            self.col.find(query)
            .sort([("created_at", DESCENDING), ("_id", DESCENDING)])
            .limit(limit + 1)
            .to_list(length=limit + 1)
        )

        next_cursor: str | None = None
        if len(docs) > limit:
            last = docs[limit - 1]
            next_cursor = encode_cursor(
                created_at=last["created_at"],
                id=str(last["_id"]),
            )
            docs = docs[:limit]

        return docs, next_cursor

    async def has_accepted_permission(self, *, user_a: str, user_b: str) -> bool:
        doc = await self.col.find_one(
            {
                "pair_id": pair_id_for(user_a, user_b),
                "status": "accepted",
            },
            {"_id": 1},
        )
        return doc is not None