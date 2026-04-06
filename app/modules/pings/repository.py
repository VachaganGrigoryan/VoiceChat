from __future__ import annotations

from datetime import datetime, UTC
from typing import Any

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import DESCENDING, ReturnDocument
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

    async def get_pair_states(self, *, user_id: str, peer_user_ids: list[str]) -> dict[str, dict[str, Any]]:
        if not peer_user_ids:
            return {}

        pair_ids = [pair_id_for(user_id, peer_user_id) for peer_user_id in dict.fromkeys(peer_user_ids)]
        docs = await self.col.find({"pair_id": {"$in": pair_ids}}).to_list(length=len(pair_ids))
        return {doc["pair_id"]: doc for doc in docs}

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

    async def reopen_ping(
        self,
        *,
        ping_id: str,
        from_user_id: str,
        to_user_id: str,
    ) -> dict[str, Any] | None:
        now = datetime.now(UTC)
        return await self.col.find_one_and_update(
            {
                "_id": _oid(ping_id),
                "status": {"$in": ["cancelled", "declined"]},
            },
            {
                "$set": {
                    "from_user_id": from_user_id,
                    "to_user_id": to_user_id,
                    "status": "pending",
                    "created_at": now,
                    "updated_at": now,
                    "responded_at": None,
                }
            },
            return_document=ReturnDocument.AFTER,
        )

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

    async def is_blocked(self, *, user_a: str, user_b: str) -> bool:
        doc = await self.col.find_one(
            {
                "pair_id": pair_id_for(user_a, user_b),
                "status": "blocked",
            },
            {"_id": 1},
        )
        return doc is not None

    async def cancel_pending(self, *, ping_id: str, by_user_id: str) -> dict[str, Any]:
        now = datetime.now(UTC)
        res = await self.col.find_one_and_update(
            {
                "_id": _oid(ping_id),
                "from_user_id": by_user_id,
                "status": "pending",
            },
            {
                "$set": {
                    "status": "cancelled",
                    "updated_at": now,
                    "responded_at": now,
                }
            },
            return_document=True,
        )
        if not res:
            raise AppError(code="PING_NOT_CANCELLABLE", message="Ping cannot be cancelled", status_code=400)
        return res

    async def block_pair(self, *, user_a: str, user_b: str, by_user_id: str) -> dict[str, Any]:
        now = datetime.now(UTC)
        pair_id = pair_id_for(user_a, user_b)

        doc = await self.col.find_one({"pair_id": pair_id})
        if doc:
            await self.col.update_one(
                {"_id": doc["_id"]},
                {
                    "$set": {
                        "status": "blocked",
                        "updated_at": now,
                        "responded_at": now,
                        "blocked_by": by_user_id,
                    }
                },
            )
            return await self.col.find_one({"_id": doc["_id"]})

        new_doc = {
            "pair_id": pair_id,
            "from_user_id": user_a,
            "to_user_id": user_b,
            "status": "blocked",
            "blocked_by": by_user_id,
            "created_at": now,
            "updated_at": now,
            "responded_at": now,
        }
        result = await self.col.insert_one(new_doc)
        new_doc["_id"] = result.inserted_id
        return new_doc

    async def unblock_pair(self, *, user_a: str, user_b: str, by_user_id: str) -> dict[str, Any]:
        now = datetime.now(UTC)
        res = await self.col.find_one_and_update(
            {
                "pair_id": pair_id_for(user_a, user_b),
                "status": "blocked",
                "blocked_by": by_user_id,
            },
            {
                "$set": {
                    "status": "cancelled",  # or "none" if you introduce it later
                    "updated_at": now,
                },
                "$unset": {"blocked_by": ""},
            },
            return_document=True,
        )
        if not res:
            raise AppError(code="PAIR_NOT_BLOCKED", message="Pair is not blocked by this user", status_code=400)
        return res

    async def delete_pair(self, *, user_a: str, user_b: str) -> bool:
        result = await self.col.delete_one({"pair_id": pair_id_for(user_a, user_b)})
        return result.deleted_count > 0

    async def list_blocked(self, *, user_id: str) -> list[dict[str, Any]]:
        return await self.col.find(
            {
                "status": "blocked",
                "$or": [{"from_user_id": user_id}, {"to_user_id": user_id}],
            }
        ).sort("updated_at", -1).to_list(length=100)
