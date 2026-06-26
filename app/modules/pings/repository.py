from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from beanie.operators import Eq, In
from pymongo.errors import DuplicateKeyError

from app.core.errors import AppError
from app.core.pagination.cursor import decode_cursor, encode_cursor
from app.db.models import PingDocument
from app.db.object_id import parse_object_id as _oid
from app.db.repository import BaseRepository
from app.modules.pings.schemas import PingStatus


def pair_id_for(user_a: str, user_b: str) -> str:
    a = str(user_a)
    b = str(user_b)
    return f"{a}_{b}" if a < b else f"{b}_{a}"


class PingsRepository(BaseRepository[PingDocument]):
    model = PingDocument

    async def create_ping(
        self, *, from_user_id: str, to_user_id: str, status: PingStatus = "pending"
    ) -> PingDocument:
        now = datetime.now(UTC)
        doc = PingDocument(
            pair_id=pair_id_for(from_user_id, to_user_id),
            from_user_id=from_user_id,
            to_user_id=to_user_id,
            status=status,
            created_at=now,
            updated_at=now,
        )
        try:
            await doc.insert()
            return doc
        except DuplicateKeyError as exc:
            raise AppError(
                code="PING_ALREADY_EXISTS",
                message="Ping already exists",
                status_code=409,
            ) from exc

    async def find_by_id(self, ping_id: str) -> PingDocument | None:
        return await self.get_by_id(ping_id)

    async def find_by_pair_id(self, pair_id: str) -> PingDocument | None:
        return await PingDocument.find_one(PingDocument.pair_id == pair_id)

    async def get_pair_state(self, *, user_a: str, user_b: str) -> PingDocument | None:
        return await PingDocument.find_one(
            PingDocument.pair_id == pair_id_for(user_a, user_b)
        )

    async def get_pair_states(
        self, *, user_id: str, peer_user_ids: list[str]
    ) -> dict[str, PingDocument]:
        if not peer_user_ids:
            return {}

        pair_ids = [
            pair_id_for(user_id, peer_user_id)
            for peer_user_id in dict.fromkeys(peer_user_ids)
        ]
        docs = await PingDocument.find(In(PingDocument.pair_id, pair_ids)).to_list()
        return {doc.pair_id: doc for doc in docs}

    async def update_status(
        self, *, ping_id: str, status: PingStatus
    ) -> PingDocument | None:
        now = datetime.now(UTC)
        responded_at = (
            now
            if status in {"accepted", "declined", "cancelled", "expired", "blocked"}
            else None
        )
        ping = await PingDocument.get(_oid(ping_id))
        if ping is None:
            return None
        return await ping.set(
            {
                PingDocument.status: status,
                PingDocument.updated_at: now,
                PingDocument.responded_at: responded_at,
            }
        )

    async def reopen_ping(
        self,
        *,
        ping_id: str,
        from_user_id: str,
        to_user_id: str,
    ) -> PingDocument | None:
        now = datetime.now(UTC)
        return await self.find_one_and_update(
            {"_id": _oid(ping_id), "status": {"$in": ["cancelled", "declined"]}},
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
        )

    async def _list_by_cursor(
        self,
        *,
        owner_field: str,
        user_id: str,
        limit: int,
        cursor: str | None,
    ) -> tuple[list[PingDocument], str | None]:
        query: dict[str, Any] = {owner_field: user_id}
        if cursor:
            payload = decode_cursor(cursor, required_fields={"created_at", "id"})
            created_at = payload["created_at"]
            oid = _oid(payload["id"])
            query["$or"] = [
                {"created_at": {"$lt": created_at}},
                {"created_at": created_at, "_id": {"$lt": oid}},
            ]

        docs = (
            await PingDocument.find(query)
            .sort("-created_at", "-_id")
            .limit(limit + 1)
            .to_list()
        )

        next_cursor: str | None = None
        if len(docs) > limit:
            last = docs[limit - 1]
            next_cursor = encode_cursor(
                created_at=last.created_at, id=str(last.str_id)
            )
            docs = docs[:limit]
        return docs, next_cursor

    async def list_incoming(
        self, *, user_id: str, limit: int = 20, cursor: str | None = None
    ) -> tuple[list[PingDocument], str | None]:
        return await self._list_by_cursor(
            owner_field="to_user_id", user_id=user_id, limit=limit, cursor=cursor
        )

    async def list_outgoing(
        self, *, user_id: str, limit: int = 20, cursor: str | None = None
    ) -> tuple[list[PingDocument], str | None]:
        return await self._list_by_cursor(
            owner_field="from_user_id", user_id=user_id, limit=limit, cursor=cursor
        )

    async def has_accepted_permission(self, *, user_a: str, user_b: str) -> bool:
        return (
            await PingDocument.find_one(
                PingDocument.pair_id == pair_id_for(user_a, user_b),
                PingDocument.status == "accepted",
            )
            is not None
        )

    async def is_blocked(self, *, user_a: str, user_b: str) -> bool:
        return (
            await PingDocument.find_one(
                PingDocument.pair_id == pair_id_for(user_a, user_b),
                PingDocument.status == "blocked",
            )
            is not None
        )

    async def cancel_pending(self, *, ping_id: str, by_user_id: str) -> PingDocument:
        now = datetime.now(UTC)
        res = await self.find_one_and_update(
            {"_id": _oid(ping_id), "from_user_id": by_user_id, "status": "pending"},
            {"$set": {"status": "cancelled", "updated_at": now, "responded_at": now}},
        )
        if res is None:
            raise AppError(
                code="PING_NOT_CANCELLABLE",
                message="Ping cannot be cancelled",
                status_code=400,
            )
        return res

    async def block_pair(
        self, *, user_a: str, user_b: str, by_user_id: str
    ) -> PingDocument:
        now = datetime.now(UTC)
        pair_id = pair_id_for(user_a, user_b)

        existing = await PingDocument.find_one(PingDocument.pair_id == pair_id)
        if existing:
            return await existing.set(
                {
                    PingDocument.status: "blocked",
                    PingDocument.updated_at: now,
                    PingDocument.responded_at: now,
                    "blocked_by": by_user_id,
                }
            )

        doc = PingDocument(
            pair_id=pair_id,
            from_user_id=user_a,
            to_user_id=user_b,
            status="blocked",
            created_at=now,
            updated_at=now,
            responded_at=now,
            blocked_by=by_user_id,
        )
        await doc.insert()
        return doc

    async def unblock_pair(
        self, *, user_a: str, user_b: str, by_user_id: str
    ) -> PingDocument:
        now = datetime.now(UTC)
        res = await self.find_one_and_update(
            {
                "pair_id": pair_id_for(user_a, user_b),
                "status": "blocked",
                "blocked_by": by_user_id,
            },
            {
                "$set": {"status": "cancelled", "updated_at": now},
                "$unset": {"blocked_by": ""},
            },
        )
        if res is None:
            raise AppError(
                code="PAIR_NOT_BLOCKED",
                message="Pair is not blocked by this user",
                status_code=400,
            )
        return res

    async def delete_pair(self, *, user_a: str, user_b: str) -> bool:
        result = await PingDocument.find(
            PingDocument.pair_id == pair_id_for(user_a, user_b)
        ).delete()
        return bool(result and result.deleted_count > 0)

    async def list_blocked(self, *, user_id: str) -> list[PingDocument]:
        return (
            await PingDocument.find(
                Eq(PingDocument.status, "blocked"),
                {"$or": [{"from_user_id": user_id}, {"to_user_id": user_id}]},
            )
            .sort("-updated_at")
            .limit(100)
            .to_list()
        )
