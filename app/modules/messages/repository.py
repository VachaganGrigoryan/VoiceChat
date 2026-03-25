from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Optional

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ReturnDocument

from app.core.errors import AppError
from app.core.pagination.cursor import decode_cursor, encode_cursor
from app.db.indexes import COL_MESSAGES
from app.modules.messages.mappers import message_is_deleted, normalize_message_record

REACTION_MAX_GROUPS = 10
REACTION_UPDATE_RETRIES = 3
REPLY_PREVIEW_MAX_TEXT = 160


def _oid(s: str) -> ObjectId:
    try:
        return ObjectId(s)
    except Exception:
        raise AppError(code="INVALID_ID", message="Invalid id", status_code=400)


def _id_str(value: Any) -> str:
    if isinstance(value, ObjectId):
        return str(value)
    return str(value)


def _truncate_preview_text(value: str | None) -> str | None:
    if value is None:
        return None

    normalized = value.strip()
    if not normalized:
        return None
    if len(normalized) <= REPLY_PREVIEW_MAX_TEXT:
        return normalized
    return normalized[: REPLY_PREVIEW_MAX_TEXT - 3].rstrip() + "..."


def _build_reply_preview(message: dict[str, Any]) -> dict[str, Any]:
    is_deleted = message_is_deleted(message)
    message_type, media = normalize_message_record(message)
    return {
        "message_id": str(message["_id"]),
        "sender_id": _id_str(message["sender_id"]),
        "type": message_type,
        "media_kind": media.get("kind") if media is not None else None,
        "text": None if is_deleted else _truncate_preview_text(message.get("text")),
        "is_deleted": is_deleted,
    }


def _message_participants(message: dict[str, Any]) -> set[str]:
    return {
        _id_str(message["sender_id"]),
        _id_str(message["receiver_id"]),
    }


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

    def _assert_message_participant(
        self, *, message: dict[str, Any], user_id: str
    ) -> None:
        if user_id not in _message_participants(message):
            raise AppError(
                code="MESSAGE_NOT_FOUND", message="Message not found", status_code=404
            )

    async def _load_reply_target(
        self,
        *,
        sender_id: str,
        receiver_id: str,
        reply_to_message_id: str,
    ) -> dict[str, Any]:
        conv_id = conversation_id_for(sender_id, receiver_id)
        target = await self.col.find_one({"_id": _oid(reply_to_message_id)})
        if not target:
            raise AppError(
                code="MESSAGE_NOT_FOUND", message="Message not found", status_code=404
            )
        if target.get("conversation_id") != conv_id:
            raise AppError(
                code="INVALID_REPLY_TARGET",
                message="Reply target must belong to the same conversation",
                status_code=400,
            )

        participants = _message_participants(target)
        if {sender_id, receiver_id} != participants:
            raise AppError(
                code="INVALID_REPLY_TARGET",
                message="Reply target must belong to the same conversation",
                status_code=400,
            )

        return target

    async def _resolve_thread_root(
        self,
        *,
        message_id: str,
        user_id: str,
    ) -> tuple[dict[str, Any], str]:
        message = await self.col.find_one(
            {
                "_id": _oid(message_id),
                "hidden_for_user_ids": {"$ne": user_id},
            }
        )
        if not message:
            raise AppError(
                code="MESSAGE_NOT_FOUND", message="Message not found", status_code=404
            )
        self._assert_message_participant(message=message, user_id=user_id)

        thread_root_id = str(message.get("thread_root_id") or message["_id"])
        return message, thread_root_id

    async def _replace_reactions_with_retry(
        self,
        *,
        message_id: str,
        user_id: str,
        emoji: str,
        remove_only: bool,
    ) -> dict[str, Any]:
        normalized_emoji = emoji.strip()
        if not normalized_emoji:
            raise AppError(
                code="INVALID_EMOJI", message="emoji is required", status_code=400
            )

        message_oid = _oid(message_id)
        for _ in range(REACTION_UPDATE_RETRIES):
            existing = await self.col.find_one({"_id": message_oid})
            if not existing:
                raise AppError(
                    code="MESSAGE_NOT_FOUND",
                    message="Message not found",
                    status_code=404,
                )

            self._assert_message_participant(message=existing, user_id=user_id)
            if user_id in existing.get("hidden_for_user_ids", []):
                raise AppError(
                    code="MESSAGE_NOT_REACTABLE",
                    message="Hidden messages cannot be reacted to",
                    status_code=400,
                )
            if message_is_deleted(existing):
                raise AppError(
                    code="MESSAGE_NOT_REACTABLE",
                    message="Deleted messages cannot be reacted to",
                    status_code=400,
                )

            now = datetime.now(UTC)
            reactions: list[dict[str, Any]] = []
            found_group = False
            changed = False

            for reaction in existing.get("reactions", []):
                if reaction.get("emoji") != normalized_emoji:
                    reactions.append(
                        {
                            **reaction,
                            "user_ids": [
                                str(uid) for uid in reaction.get("user_ids", [])
                            ],
                        }
                    )
                    continue

                found_group = True
                user_ids = [str(uid) for uid in reaction.get("user_ids", [])]
                has_reaction = user_id in user_ids

                if has_reaction:
                    user_ids = [uid for uid in user_ids if uid != user_id]
                    changed = True
                elif remove_only:
                    user_ids = user_ids
                else:
                    user_ids.append(user_id)
                    changed = True

                if user_ids:
                    reactions.append(
                        {
                            "emoji": normalized_emoji,
                            "user_ids": user_ids,
                            "count": len(user_ids),
                            "updated_at": (
                                now if changed else reaction.get("updated_at") or now
                            ),
                        }
                    )

            if not found_group and not remove_only:
                if len(existing.get("reactions", [])) >= REACTION_MAX_GROUPS:
                    raise AppError(
                        code="REACTION_LIMIT_EXCEEDED",
                        message="A message can have at most 10 distinct reactions",
                        status_code=400,
                    )
                reactions.append(
                    {
                        "emoji": normalized_emoji,
                        "user_ids": [user_id],
                        "count": 1,
                        "updated_at": now,
                    }
                )
                changed = True

            update_doc = {"reactions": reactions}
            if changed:
                update_doc["updated_at"] = now

            updated = await self.col.find_one_and_update(
                {
                    "_id": existing["_id"],
                    "updated_at": existing["updated_at"],
                },
                {"$set": update_doc},
                return_document=ReturnDocument.AFTER,
            )
            if updated is not None:
                return updated

        raise AppError(
            code="REACTION_UPDATE_CONFLICT",
            message="Reaction update conflict",
            status_code=409,
        )

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
            "media": media,
            "hidden_for_user_ids": [],
            "status": "sent",
            "edited_at": None,
            "delivered_at": None,
            "read_at": None,
            "reply_mode": None,
            "reply_to_message_id": None,
            "thread_root_id": None,
            "reply_preview": None,
            "is_thread_root": False,
            "thread_reply_count": 0,
            "last_thread_reply_at": None,
            "reactions": [],
            "created_at": now,
            "updated_at": now,
        }

        res = await self.col.insert_one(doc)
        doc["_id"] = res.inserted_id
        return doc

    async def create_quote_reply(
        self,
        *,
        sender_id: str,
        receiver_id: str,
        message_type: str,
        reply_to_message_id: str,
        text: str | None = None,
        media: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        target = await self._load_reply_target(
            sender_id=sender_id,
            receiver_id=receiver_id,
            reply_to_message_id=reply_to_message_id,
        )
        doc = await self.create_message(
            sender_id=sender_id,
            receiver_id=receiver_id,
            message_type=message_type,
            text=text,
            media=media,
        )

        updated = await self.col.find_one_and_update(
            {"_id": doc["_id"]},
            {
                "$set": {
                    "reply_mode": "quote",
                    "reply_to_message_id": str(target["_id"]),
                    "reply_preview": _build_reply_preview(target),
                }
            },
            return_document=ReturnDocument.AFTER,
        )
        if updated is None:
            raise AppError(
                code="MESSAGE_CREATE_FAILED",
                message="Failed to create message",
                status_code=500,
            )
        return updated

    async def create_thread_reply(
        self,
        *,
        sender_id: str,
        receiver_id: str,
        message_type: str,
        reply_to_message_id: str,
        text: str | None = None,
        media: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        target = await self._load_reply_target(
            sender_id=sender_id,
            receiver_id=receiver_id,
            reply_to_message_id=reply_to_message_id,
        )
        thread_root_id = str(target.get("thread_root_id") or target["_id"])
        now = datetime.now(UTC)

        doc = {
            "conversation_id": target["conversation_id"],
            "sender_id": _oid(sender_id),
            "receiver_id": _oid(receiver_id),
            "type": message_type,
            "text": text,
            "media": media,
            "hidden_for_user_ids": [],
            "status": "sent",
            "edited_at": None,
            "delivered_at": None,
            "read_at": None,
            "reply_mode": "thread",
            "reply_to_message_id": str(target["_id"]),
            "thread_root_id": thread_root_id,
            "reply_preview": _build_reply_preview(target),
            "is_thread_root": False,
            "thread_reply_count": 0,
            "last_thread_reply_at": None,
            "reactions": [],
            "created_at": now,
            "updated_at": now,
        }

        res = await self.col.insert_one(doc)
        doc["_id"] = res.inserted_id

        root = await self.col.find_one_and_update(
            {
                "_id": _oid(thread_root_id),
                "conversation_id": target["conversation_id"],
            },
            {
                "$set": {
                    "is_thread_root": True,
                    "last_thread_reply_at": now,
                    "updated_at": now,
                },
                "$inc": {"thread_reply_count": 1},
            },
            return_document=ReturnDocument.AFTER,
        )
        if root is None:
            raise AppError(
                code="THREAD_ROOT_NOT_FOUND",
                message="Thread root not found",
                status_code=404,
            )

        return doc

    async def create_voice_message(
        self,
        *,
        sender_id: str,
        receiver_id: str,
        audio: dict[str, Any],
    ) -> dict[str, Any]:
        media = dict(audio)
        media["kind"] = "voice"
        return await self.create_message(
            sender_id=sender_id,
            receiver_id=receiver_id,
            message_type="media",
            media=media,
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
            raise AppError(
                code="INVALID_LIMIT",
                message="limit must be between 1 and 100",
                status_code=400,
            )

        uid = _oid(user_id)

        pipeline: list[dict[str, Any]] = [
            {
                "$match": {
                    "thread_root_id": None,
                    "hidden_for_user_ids": {"$ne": user_id},
                    "$or": [
                        {"sender_id": uid},
                        {"receiver_id": uid},
                    ],
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

    async def load_thread_messages(
        self,
        *,
        message_id: str,
        user_id: str,
    ) -> list[dict[str, Any]]:
        message, thread_root_id = await self._resolve_thread_root(
            message_id=message_id,
            user_id=user_id,
        )
        cur = self.col.find(
            {
                "conversation_id": message["conversation_id"],
                "thread_root_id": thread_root_id,
                "hidden_for_user_ids": {"$ne": user_id},
            }
        ).sort([("created_at", 1), ("_id", 1)])
        return await cur.to_list(length=None)

    async def load_thread_summary(
        self,
        *,
        message_id: str,
        user_id: str,
    ) -> dict[str, Any]:
        _, thread_root_id = await self._resolve_thread_root(
            message_id=message_id,
            user_id=user_id,
        )
        root = await self.col.find_one(
            {
                "_id": _oid(thread_root_id),
                "hidden_for_user_ids": {"$ne": user_id},
            }
        )
        if not root:
            raise AppError(
                code="THREAD_ROOT_NOT_FOUND",
                message="Thread root not found",
                status_code=404,
            )
        self._assert_message_participant(message=root, user_id=user_id)
        return root

    async def add_or_toggle_grouped_reaction(
        self,
        *,
        message_id: str,
        user_id: str,
        emoji: str,
    ) -> dict[str, Any]:
        return await self._replace_reactions_with_retry(
            message_id=message_id,
            user_id=user_id,
            emoji=emoji,
            remove_only=False,
        )

    async def remove_grouped_reaction(
        self,
        *,
        message_id: str,
        user_id: str,
        emoji: str,
    ) -> dict[str, Any]:
        return await self._replace_reactions_with_retry(
            message_id=message_id,
            user_id=user_id,
            emoji=emoji,
            remove_only=True,
        )

    async def mark_delivered_for_receiver(
        self, *, message_id: str, receiver_id: str
    ) -> dict[str, Any]:
        receiver_oid = _oid(receiver_id)
        query = {
            "_id": _oid(message_id),
            "receiver_id": receiver_oid,
            "hidden_for_user_ids": {"$ne": receiver_id},
        }
        existing = await self.col.find_one(query)
        if not existing:
            raise AppError(
                code="MESSAGE_NOT_FOUND", message="Message not found", status_code=404
            )
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
            return_document=ReturnDocument.AFTER,
        )
        if not res:
            raise AppError(
                code="MESSAGE_NOT_FOUND", message="Message not found", status_code=404
            )
        return res

    async def mark_read_for_receiver(
        self, *, message_id: str, receiver_id: str
    ) -> dict[str, Any]:
        receiver_oid = _oid(receiver_id)
        query = {
            "_id": _oid(message_id),
            "receiver_id": receiver_oid,
            "hidden_for_user_ids": {"$ne": receiver_id},
        }
        existing = await self.col.find_one(query)
        if not existing:
            raise AppError(
                code="MESSAGE_NOT_FOUND", message="Message not found", status_code=404
            )
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
            return_document=ReturnDocument.AFTER,
        )
        if not res:
            raise AppError(
                code="MESSAGE_NOT_FOUND", message="Message not found", status_code=404
            )
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
                "thread_root_id": None,
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
        await self.col.update_many(
            {
                "conversation_id": conversation_id,
                "thread_root_id": None,
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
            return_document=ReturnDocument.AFTER,
        )
        if not res:
            raise AppError(
                code="MESSAGE_NOT_EDITABLE",
                message="Message cannot be edited",
                status_code=400,
            )
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
            raise AppError(
                code="MESSAGE_NOT_FOUND", message="Message not found", status_code=404
            )

        now = datetime.now(UTC)
        await self.col.update_many(
            {"reply_to_message_id": message_id},
            {
                "$set": {
                    "reply_preview.text": None,
                    "reply_preview.is_deleted": True,
                    "updated_at": now,
                }
            },
        )
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
            return_document=ReturnDocument.AFTER,
        )
        if not res:
            raise AppError(
                code="MESSAGE_NOT_FOUND", message="Message not found", status_code=404
            )
        return res
