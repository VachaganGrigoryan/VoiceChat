from __future__ import annotations

from typing import Any

from app.core.errors import AppError
from app.core.pagination.cursor import decode_cursor, encode_cursor
from app.db.models import MessageDocument
from app.db.object_id import parse_object_id as _oid


class HistoryRepositoryMixin:
    async def list_history_for_conversation(
        self,
        *,
        conversation_id: str,
        user_id: str,
        limit: int = 20,
        cursor: str | None = None,
    ) -> tuple[list[MessageDocument], str | None]:
        if limit < 1 or limit > 100:
            raise AppError(
                code="INVALID_LIMIT",
                message="limit must be between 1 and 100",
                status_code=400,
            )

        q: dict[str, Any] = {
            "conversation_id": conversation_id,
            "thread_root_id": None,
            "hidden_for_user_ids": {"$ne": user_id},
        }

        if cursor:
            cursor_data = decode_cursor(
                cursor,
                required_fields={"created_at", "message_id"},
            )
            cursor_created_at = cursor_data["created_at"]
            cursor_message_id = str(cursor_data["message_id"])
            q["$or"] = [
                {"created_at": {"$lt": cursor_created_at}},
                {
                    "$and": [
                        {"created_at": cursor_created_at},
                        {"_id": {"$lt": _oid(cursor_message_id)}},
                    ]
                },
            ]

        cur = self.col.find(q).sort([("created_at", -1), ("_id", -1)]).limit(limit + 1)
        items = await cur.to_list(length=limit + 1)

        next_cursor: str | None = None
        if len(items) > limit:
            last_visible = items[limit - 1]
            next_cursor = encode_cursor(
                created_at=last_visible["created_at"],
                message_id=str(last_visible["_id"]),
            )
            items = items[:limit]

        return [MessageDocument.model_validate(item) for item in items], next_cursor

    async def get_by_id(self, *, message_id: str) -> MessageDocument | None:
        return await MessageDocument.get(_oid(message_id))

    async def get_by_id_for_conversation(
        self, *, conversation_id: str, message_id: str, user_id: str
    ) -> MessageDocument | None:
        raw = await self.col.find_one(
            {
                "_id": _oid(message_id),
                "conversation_id": conversation_id,
                "hidden_for_user_ids": {"$ne": user_id},
            }
        )
        return MessageDocument.model_validate(raw) if raw is not None else None
