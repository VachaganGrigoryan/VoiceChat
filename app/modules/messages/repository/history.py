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
            # Scheduled messages are withheld from the timeline until released.
            "state": {"$ne": "scheduled"},
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

    async def list_feed_for_conversations(
        self,
        *,
        conversation_ids: list[str],
        limit: int = 20,
        cursor: str | None = None,
    ) -> tuple[list[MessageDocument], str | None]:
        """Newest-first top-level posts across several channels (feed aggregate).

        Unlike ``list_history_for_conversation`` this spans multiple conversations
        and applies no per-user ``hidden_for`` filter — feed viewers are not
        members, so they have no hidden entries. Excludes thread replies and
        scheduled messages. Cursor is ``(created_at, message_id)``, same shape as
        the single-conversation history cursor.
        """
        if limit < 1 or limit > 100:
            raise AppError(
                code="INVALID_LIMIT",
                message="limit must be between 1 and 100",
                status_code=400,
            )
        if not conversation_ids:
            return [], None

        q: dict[str, Any] = {
            "conversation_id": {"$in": conversation_ids},
            "thread_root_id": None,
            "state": {"$ne": "scheduled"},
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

    async def search_messages(
        self,
        *,
        conversation_ids: list[str],
        user_id: str,
        query: str,
        limit: int,
        skip: int,
    ) -> tuple[list[MessageDocument], bool]:
        """Full-text search over message plaintext in accessible conversations.

        Returns the page plus a ``has_more`` flag. Deleted messages are absent
        (hard-deleted), and messages hidden for the caller or still scheduled are
        excluded from results.
        """
        if not conversation_ids:
            return [], False

        q: dict[str, Any] = {
            "$text": {"$search": query},
            "conversation_id": {"$in": conversation_ids},
            "hidden_for_user_ids": {"$ne": user_id},
            "state": {"$ne": "scheduled"},
        }
        cur = (
            self.col.find(q, {"score": {"$meta": "textScore"}})
            .sort([("score", {"$meta": "textScore"})])
            .skip(skip)
            .limit(limit + 1)
        )
        items = await cur.to_list(length=limit + 1)
        has_more = len(items) > limit
        return (
            [MessageDocument.model_validate(item) for item in items[:limit]],
            has_more,
        )

    async def list_by_ids_for_conversation(
        self, *, conversation_id: str, message_ids: list[str], user_id: str
    ) -> list[MessageDocument]:
        """Fetch a set of messages in a conversation, preserving ``message_ids`` order."""
        if not message_ids:
            return []
        oids = [_oid(mid) for mid in message_ids]
        raw = await self.col.find(
            {
                "_id": {"$in": oids},
                "conversation_id": conversation_id,
                "hidden_for_user_ids": {"$ne": user_id},
            }
        ).to_list(length=None)
        by_id = {str(item["_id"]): item for item in raw}
        return [
            MessageDocument.model_validate(by_id[mid])
            for mid in message_ids
            if mid in by_id
        ]

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
