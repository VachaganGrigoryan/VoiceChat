from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.core.errors import AppError
from app.core.pagination.cursor import decode_cursor, encode_cursor
from app.db.models import MessageDocument
from app.db.object_id import parse_object_id as _oid
from app.modules.messages.repository.helpers import ConversationListRow, conversation_id_for


class HistoryRepositoryMixin:
    async def list_history(
        self,
        *,
        user_id: str,
        peer_user_id: str,
        limit: int = 20,
        cursor: str | None = None,
    ) -> tuple[list[MessageDocument], str | None]:
        """
        Returns newest-first messages.
        Cursor is an ISO datetime string (created_at) for pagination.
        Query: created_at < cursor (older messages)
        """
        if limit < 1 or limit > 100:
            raise AppError(
                code="INVALID_LIMIT",
                message="limit must be between 1 and 100",
                status_code=400,
            )

        conv_id = conversation_id_for(user_id, peer_user_id)
        q: dict[str, Any] = {
            "conversation_id": conv_id,
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

    async def list_conversations_for_user(
        self,
        *,
        user_id: str,
        limit: int = 50,
        cursor: str | None = None,
    ) -> tuple[list[ConversationListRow], str | None]:
        if limit < 1 or limit > 100:
            raise AppError(
                code="INVALID_LIMIT",
                message="limit must be between 1 and 100",
                status_code=400,
            )

        uid = user_id
        pipeline: list[dict[str, Any]] = [
            {
                "$match": {
                    "thread_root_id": None,
                    "hidden_for_user_ids": {"$ne": user_id},
                    "$or": [{"sender_id": uid}, {"receiver_id": uid}],
                }
            },
            {"$sort": {"created_at": -1, "_id": -1}},
            {
                "$group": {
                    "_id": "$conversation_id",
                    "last_message": {"$first": "$$ROOT"},
                    "unread_count": {
                        "$sum": {
                            "$cond": [
                                {
                                    "$and": [
                                        {"$eq": ["$receiver_id", uid]},
                                        {"$ne": ["$status", "read"]},
                                    ]
                                },
                                1,
                                0,
                            ]
                        }
                    },
                }
            },
        ]

        if cursor:
            cursor_data = decode_cursor(
                cursor,
                required_fields={"created_at", "conversation_id"},
            )
            cursor_created_at = cursor_data["created_at"]
            cursor_conversation_id = str(cursor_data["conversation_id"])
            pipeline.append(
                {
                    "$match": {
                        "$or": [
                            {"last_message.created_at": {"$lt": cursor_created_at}},
                            {
                                "$and": [
                                    {"last_message.created_at": cursor_created_at},
                                    {"_id": {"$gt": cursor_conversation_id}},
                                ]
                            },
                        ]
                    }
                }
            )

        pipeline.extend(
            [
                {"$sort": {"last_message.created_at": -1, "_id": 1}},
                {"$limit": limit + 1},
            ]
        )

        aggregation_cursor = await self.col.aggregate(pipeline)
        rows = await aggregation_cursor.to_list(length=limit + 1)

        next_cursor: str | None = None
        if len(rows) > limit:
            last_visible = rows[limit - 1]
            next_cursor = encode_cursor(
                created_at=last_visible["last_message"]["created_at"],
                conversation_id=last_visible["_id"],
            )
            rows = rows[:limit]

        return [
            ConversationListRow(
                conversation_id=str(row["_id"]),
                last_message=MessageDocument.model_validate(row["last_message"]),
                unread_count=int(row.get("unread_count", 0)),
            )
            for row in rows
        ], next_cursor

    async def get_by_id(self, *, message_id: str) -> MessageDocument | None:
        return await MessageDocument.get(_oid(message_id))

    async def mark_delivered_for_receiver(
        self, *, message_id: str, receiver_id: str
    ) -> MessageDocument:
        existing = await MessageDocument.find_one(
            {
                "_id": _oid(message_id),
                "receiver_id": receiver_id,
                "hidden_for_user_ids": {"$ne": receiver_id},
            }
        )
        if existing is None:
            raise AppError(
                code="MESSAGE_NOT_FOUND", message="Message not found", status_code=404
            )
        if existing.status in {"delivered", "read"}:
            return existing

        now = datetime.now(UTC)
        return await existing.set(
            {"status": "delivered", "delivered_at": now, "updated_at": now}
        )

    async def mark_read_for_receiver(
        self, *, message_id: str, receiver_id: str
    ) -> MessageDocument:
        existing = await MessageDocument.find_one(
            {
                "_id": _oid(message_id),
                "receiver_id": receiver_id,
                "hidden_for_user_ids": {"$ne": receiver_id},
            }
        )
        if existing is None:
            raise AppError(
                code="MESSAGE_NOT_FOUND", message="Message not found", status_code=404
            )
        if existing.status == "read":
            return existing

        now = datetime.now(UTC)
        return await existing.set(
            {
                "status": "read",
                "read_at": now,
                "delivered_at": existing.delivered_at or now,
                "updated_at": now,
            }
        )

    async def mark_conversation_read_for_receiver(
        self,
        *,
        receiver_id: str,
        peer_user_id: str,
    ) -> int:
        now = datetime.now(UTC)
        conversation_id = conversation_id_for(receiver_id, peer_user_id)
        result = await self.col.update_many(
            {
                "conversation_id": conversation_id,
                "thread_root_id": None,
                "receiver_id": receiver_id,
                "hidden_for_user_ids": {"$ne": receiver_id},
                "status": {"$ne": "read"},
            },
            {"$set": {"status": "read", "read_at": now, "updated_at": now}},
        )
        await self.col.update_many(
            {
                "conversation_id": conversation_id,
                "thread_root_id": None,
                "receiver_id": receiver_id,
                "hidden_for_user_ids": {"$ne": receiver_id},
                "delivered_at": None,
                "read_at": {"$ne": None},
            },
            {"$set": {"delivered_at": now, "updated_at": now}},
        )
        return result.modified_count
