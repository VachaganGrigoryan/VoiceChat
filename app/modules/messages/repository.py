from __future__ import annotations

from datetime import datetime, UTC
from typing import Any, Optional

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.errors import AppError
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

    async def create_voice_message(
        self,
        *,
        sender_id: str,
        receiver_id: str,
        audio: dict[str, Any],
    ) -> dict[str, Any]:
        sid = _oid(sender_id)
        rid = _oid(receiver_id)
        conv_id = conversation_id_for(sender_id, receiver_id)
        now = datetime.now(UTC)

        doc = {
            "conversation_id": conv_id,
            "sender_id": sid,
            "receiver_id": rid,
            "type": "voice",
            "audio": audio,  # {storage,key,url,mime,size_bytes,duration_ms?}
            "status": "sent",
            "delivered_at": None,
            "read_at": None,
            "created_at": now,
            "updated_at": now,
        }

        res = await self.col.insert_one(doc)
        doc["_id"] = res.inserted_id
        return doc

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
            raise AppError(code="INVALID_LIMIT", message="limit must be between 1 and 100", status_code=400)

        conv_id = conversation_id_for(user_id, peer_user_id)
        q: dict[str, Any] = {"conversation_id": conv_id}

        if cursor:
            try:
                cursor_dt = datetime.fromisoformat(cursor)
            except Exception:
                raise AppError(code="INVALID_CURSOR", message="cursor must be ISO datetime", status_code=400)
            q["created_at"] = {"$lt": cursor_dt}

        # newest first
        cur = self.col.find(q).sort("created_at", -1).limit(limit)
        items = await cur.to_list(length=limit)

        next_cursor = None
        if items:
            # next_cursor = created_at of last item (oldest in this page)
            last_created = items[-1]["created_at"]
            if isinstance(last_created, datetime):
                next_cursor = last_created.isoformat()

        return items, next_cursor

    async def get_by_id(self, *, message_id: str) -> Optional[dict[str, Any]]:
        return await self.col.find_one({"_id": _oid(message_id)})

    async def mark_delivered_for_receiver(self, *, message_id: str, receiver_id: str) -> dict[str, Any]:
        now = datetime.now(UTC)
        res = await self.col.find_one_and_update(
            {
                "_id": _oid(message_id),
                "receiver_id": _oid(receiver_id),
            },
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
            # Either message not found, or receiver mismatch
            raise AppError(code="MESSAGE_NOT_FOUND", message="Message not found", status_code=404)
        return res

    async def mark_read_for_receiver(self, *, message_id: str, receiver_id: str) -> dict[str, Any]:
        now = datetime.now(UTC)
        res = await self.col.find_one_and_update(
            {
                "_id": _oid(message_id),
                "receiver_id": _oid(receiver_id),
            },
            {
                "$set": {
                    "status": "read",
                    "read_at": now,
                    "updated_at": now,
                }
            },
            return_document=True,
        )
        if not res:
            raise AppError(code="MESSAGE_NOT_FOUND", message="Message not found", status_code=404)
        return res