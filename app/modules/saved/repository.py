from __future__ import annotations

from datetime import UTC, datetime

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from app.db.models import SavedMessageDocument
from app.db.repository import BaseRepository


class SavedMessagesRepository(BaseRepository[SavedMessageDocument]):
    model = SavedMessageDocument

    async def save(self, *, user_id: str, message_id: str) -> SavedMessageDocument:
        now = datetime.now(UTC)
        try:
            raw = await SavedMessageDocument.get_pymongo_collection().find_one_and_update(
                {"user_id": str(user_id), "message_id": str(message_id)},
                {
                    "$set": {
                        "saved_at": now,
                        "updated_at": now,
                    },
                    "$setOnInsert": {"created_at": now},
                },
                upsert=True,
                return_document=ReturnDocument.AFTER,
            )
        except DuplicateKeyError:  # pragma: no cover - upsert race, index resolves
            raw = await SavedMessageDocument.get_pymongo_collection().find_one(
                {"user_id": str(user_id), "message_id": str(message_id)}
            )
        return SavedMessageDocument.model_validate(raw)

    async def list_for_user(
        self, *, user_id: str, limit: int
    ) -> list[SavedMessageDocument]:
        return (
            await SavedMessageDocument.find({"user_id": str(user_id)})
            .sort("-saved_at")
            .limit(limit)
            .to_list()
        )

    async def remove(self, *, user_id: str, message_id: str) -> bool:
        result = await SavedMessageDocument.get_pymongo_collection().delete_one(
            {"user_id": str(user_id), "message_id": str(message_id)}
        )
        return result.deleted_count > 0
