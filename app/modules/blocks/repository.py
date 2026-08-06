from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pymongo.errors import DuplicateKeyError

from app.core.pagination.cursor import decode_cursor, encode_cursor
from app.db.models import BlockDocument
from app.db.object_id import parse_object_id


class BlocksRepository:
    async def find(self, *, blocker_id: str, blocked_id: str) -> BlockDocument | None:
        return await BlockDocument.find_one(
            {
                "blocker_id": str(blocker_id),
                "blocked_id": str(blocked_id),
            }
        )

    async def create(self, *, blocker_id: str, blocked_id: str) -> BlockDocument:
        now = datetime.now(UTC)
        doc = BlockDocument(
            blocker_id=str(blocker_id),
            blocked_id=str(blocked_id),
            created_at=now,
            updated_at=now,
        )
        try:
            await doc.insert()
            return doc
        except DuplicateKeyError:
            existing = await self.find(
                blocker_id=blocker_id,
                blocked_id=blocked_id,
            )
            if existing is None:  # pragma: no cover - duplicate disappeared
                raise
            return existing

    async def delete(self, *, blocker_id: str, blocked_id: str) -> BlockDocument | None:
        doc = await self.find(blocker_id=blocker_id, blocked_id=blocked_id)
        if doc is None:
            return None
        await doc.delete()
        return doc

    async def list_page(
        self,
        *,
        blocker_id: str,
        limit: int,
        cursor: str | None,
    ) -> tuple[list[BlockDocument], str | None]:
        query: dict[str, Any] = {"blocker_id": str(blocker_id)}
        if cursor:
            payload = decode_cursor(cursor, required_fields={"updated_at", "id"})
            updated_at = payload["updated_at"]
            object_id = parse_object_id(payload["id"])
            query["$or"] = [
                {"updated_at": {"$lt": updated_at}},
                {"updated_at": updated_at, "_id": {"$lt": object_id}},
            ]

        docs = (
            await BlockDocument.find(query)
            .sort("-updated_at", "-_id")
            .limit(limit + 1)
            .to_list()
        )
        next_cursor = None
        if len(docs) > limit:
            last = docs[limit - 1]
            next_cursor = encode_cursor(updated_at=last.updated_at, id=last.str_id)
            docs = docs[:limit]
        return docs, next_cursor
