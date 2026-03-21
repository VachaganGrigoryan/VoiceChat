from __future__ import annotations

from datetime import datetime, UTC
from typing import Any, Optional

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.errors import AppError
from app.core.pagination.cursor import decode_cursor, encode_cursor
from app.db.indexes import COL_MESSAGES


def _oid(s: str) -> ObjectId:
    try:
        return ObjectId(s)
    except Exception:
        raise AppError(code="INVALID_ID", message="Invalid id", status_code=400)


def conversation_id_for(user_a: str, user_b: str) -> str:
    """
    Stable conversation id: sort by string form of ObjectId.
    """
    a = str(user_a)
    b = str(user_b)
    return f"{a}_{b}" if a < b else f"{b}_{a}"


class MessagesRepository:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.col = db[COL_MESSAGES]

    async def create_message(
        self,
        *,
        sender_id: str,
        receiver_id: str,
        message_type: str,
        text: str | None = None,
        media: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        sid = _oid(sender_id)
        rid = _oid(receiver_id)
        conv_id = conversation_id_for(sender_id, receiver_id)
        now = datetime.now(UTC)

        doc = {
            "conversation_id": conv_id,
            "sender_id": sid,
            "receiver_id": rid,
            "type": message_type,
            "text": text,
            "media": media, # {storage,key,url,mime,size_bytes,duration_ms?}
            "hidden_for_user_ids": [],
            "status": "sent",
            "edited_at": None,
            "delivered_at": None,
            "read_at": None,
            "created_at": now,
            "updated_at": now,
        }

        res = await self.col.insert_one(doc)
        doc["_id"] = res.inserted_id
        return doc

    async def create_voice_message(
        self,
        *,
        sender_id: str,
        receiver_id: str,
        audio: dict[str, Any],
    ) -> dict[str, Any]:
        return await self.create_message(
            sender_id=sender_id,
            receiver_id=receiver_id,
            message_type="voice",
            media=audio,
        )

    async def list_history(
        self,
        *,
        user_id: str,
        peer_user_id: str,
        limit: int = 20,
        cursor: Optional[str] = None,
    ) -> tuple[list[dict[str, Any]], Optional[str]]:
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

        cur = (
            self.col.find(q)
            .sort([("created_at", -1), ("_id", -1)])
            .limit(limit + 1)
        )
        items = await cur.to_list(length=limit + 1)

        next_cursor: Optional[str] = None
        if len(items) > limit:
            last_visible = items[limit - 1]
            next_cursor = encode_cursor(
                created_at=last_visible["created_at"],
                message_id=str(last_visible["_id"]),
            )
            items = items[:limit]

        return items, next_cursor

    async def list_conversations_for_user(
            self,
            *,
            user_id: str,
            limit: int = 50,
            cursor: str | None = None,
    ) -> tuple[list[dict[str, Any]], str | None]:
        if limit < 1 or limit > 100:
            raise AppError(code="INVALID_LIMIT", message="limit must be between 1 and 100", status_code=400)

        uid = _oid(user_id)

        pipeline: list[dict[str, Any]] = [
            {
                "$match": {
                    "hidden_for_user_ids": {"$ne": user_id},
                    "$or": [
                        {"sender_id": uid},
                        {"receiver_id": uid},
                    ]
                }
            },
            {"$sort": {"created_at": -1}},
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

        rows = await self.col.aggregate(pipeline).to_list(length=limit + 1)

        next_cursor: str | None = None
        if len(rows) > limit:
            last_visible = rows[limit - 1]
            next_cursor = encode_cursor(
                created_at=last_visible["last_message"]["created_at"],
                conversation_id=last_visible["_id"],
            )
            rows = rows[:limit]

        return rows, next_cursor

    async def get_by_id(self, *, message_id: str) -> Optional[dict[str, Any]]:
        return await self.col.find_one({"_id": _oid(message_id)})

    async def mark_delivered_for_receiver(self, *, message_id: str, receiver_id: str) -> dict[str, Any]:
        receiver_oid = _oid(receiver_id)
        query = {
            "_id": _oid(message_id),
            "receiver_id": receiver_oid,
            "hidden_for_user_ids": {"$ne": receiver_id},
        }
        existing = await self.col.find_one(query)
        if not existing:
            raise AppError(code="MESSAGE_NOT_FOUND", message="Message not found", status_code=404)
        if existing.get("status") in {"delivered", "read"}:
            return existing

        now = datetime.now(UTC)
        res = await self.col.find_one_and_update(
            query,
            {
                "$set": {
                    "status": "delivered",
                    "delivered_at": now,
                    "updated_at": now,
                }
            },
            return_document=True,  # motor accepts True for ReturnDocument.AFTER in newer versions
        )
        if not res:
            raise AppError(code="MESSAGE_NOT_FOUND", message="Message not found", status_code=404)
        return res

    async def mark_read_for_receiver(self, *, message_id: str, receiver_id: str) -> dict[str, Any]:
        receiver_oid = _oid(receiver_id)
        query = {
            "_id": _oid(message_id),
            "receiver_id": receiver_oid,
            "hidden_for_user_ids": {"$ne": receiver_id},
        }
        existing = await self.col.find_one(query)
        if not existing:
            raise AppError(code="MESSAGE_NOT_FOUND", message="Message not found", status_code=404)
        if existing.get("status") == "read":
            return existing

        now = datetime.now(UTC)
        res = await self.col.find_one_and_update(
            query,
            {
                "$set": {
                    "status": "read",
                    "read_at": now,
                    "delivered_at": existing.get("delivered_at") or now,
                    "updated_at": now,
                }
            },
            return_document=True,
        )
        if not res:
            raise AppError(code="MESSAGE_NOT_FOUND", message="Message not found", status_code=404)
        return res

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
                "receiver_id": _oid(receiver_id),
                "hidden_for_user_ids": {"$ne": receiver_id},
                "status": {"$ne": "read"},
            },
            {
                "$set": {
                    "status": "read",
                    "read_at": now,
                    "updated_at": now,
                }
            },
        )
        # ensure delivered_at exists on newly-read messages
        await self.col.update_many(
            {
                "conversation_id": conversation_id,
                "receiver_id": _oid(receiver_id),
                "hidden_for_user_ids": {"$ne": receiver_id},
                "delivered_at": None,
                "read_at": {"$ne": None},
            },
            {"$set": {"delivered_at": now}},
        )
        return result.modified_count

    async def edit_text_message(
            self,
            *,
            message_id: str,
            sender_id: str,
            text: str,
    ) -> dict[str, Any]:
        now = datetime.now(UTC)
        res = await self.col.find_one_and_update(
            {
                "_id": _oid(message_id),
                "sender_id": _oid(sender_id),
                "type": "text",
            },
            {
                "$set": {
                    "text": text,
                    "edited_at": now,
                    "updated_at": now,
                }
            },
            return_document=True,
        )
        if not res:
            raise AppError(code="MESSAGE_NOT_EDITABLE", message="Message cannot be edited", status_code=400)
        return res

    async def hard_delete_owned_message(
            self,
            *,
            message_id: str,
            sender_id: str,
    ) -> dict[str, Any]:
        res = await self.col.find_one_and_delete(
            {
                "_id": _oid(message_id),
                "sender_id": _oid(sender_id),
            }
        )
        if not res:
            raise AppError(code="MESSAGE_NOT_FOUND", message="Message not found", status_code=404)
        return res

    async def hide_message_for_user(
            self,
            *,
            message_id: str,
            user_id: str,
    ) -> dict[str, Any]:
        now = datetime.now(UTC)
        res = await self.col.find_one_and_update(
            {
                "_id": _oid(message_id),
                "$or": [
                    {"sender_id": _oid(user_id)},
                    {"receiver_id": _oid(user_id)},
                ],
            },
            {
                "$addToSet": {"hidden_for_user_ids": user_id},
                "$set": {"updated_at": now},
            },
            return_document=True,
        )
        if not res:
            raise AppError(code="MESSAGE_NOT_FOUND", message="Message not found", status_code=404)
        return res
