from __future__ import annotations

from typing import Any

from app.core.pagination.cursor import decode_cursor, encode_cursor
from app.db.collections import COL_CONVERSATION_PARTICIPANTS
from app.db.models import ConversationDocument, MessageDocument
from app.db.object_id import parse_object_id


class ConversationsReadMixin:
    async def list_for_user(
        self,
        *,
        user_id: str,
        limit: int,
        cursor: str | None = None,
        archived: bool = False,
        folder: str | None = None,
    ) -> tuple[list[ConversationDocument], str | None]:
        """List a user's conversations, pinned first then most-recent activity.

        Ordering and grouping use the caller's own per-participant state
        (``pinned``/``archived``/``folder``) joined from ``conversation_participants``,
        so each user sees their own inbox organization. ``archived`` selects the
        archived partition; ``folder`` narrows to a single folder. The cursor
        encodes ``(pinned, activity_at, conversation_id)`` to keep the pinned-first
        order stable across pages.
        """
        pipeline: list[dict[str, Any]] = [
            {"$match": {"participant_ids": str(user_id)}},
            {
                "$addFields": {
                    "_activity_at": {"$ifNull": ["$last_message_at", "$updated_at"]},
                    "_conv_id_str": {"$toString": "$_id"},
                }
            },
            {
                "$lookup": {
                    "from": COL_CONVERSATION_PARTICIPANTS,
                    "let": {"conv_id": "$_conv_id_str"},
                    "pipeline": [
                        {
                            "$match": {
                                "$expr": {
                                    "$and": [
                                        {"$eq": ["$conversation_id", "$$conv_id"]},
                                        {"$eq": ["$user_id", str(user_id)]},
                                    ]
                                }
                            }
                        },
                        {"$project": {"pinned": 1, "archived": 1, "folder": 1}},
                    ],
                    "as": "_me",
                }
            },
            {
                "$addFields": {
                    "_pinned": {
                        "$ifNull": [{"$arrayElemAt": ["$_me.pinned", 0]}, False]
                    },
                    "_archived": {
                        "$ifNull": [{"$arrayElemAt": ["$_me.archived", 0]}, False]
                    },
                    "_folder": {"$arrayElemAt": ["$_me.folder", 0]},
                }
            },
            {"$match": {"_archived": bool(archived)}},
        ]

        if folder is not None:
            pipeline.append({"$match": {"_folder": folder}})

        if cursor is not None:
            cursor_data = decode_cursor(
                cursor,
                required_fields={"pinned", "activity_at", "conversation_id"},
            )
            cursor_pinned = bool(cursor_data["pinned"])
            activity_at = cursor_data["activity_at"]
            conversation_id = parse_object_id(str(cursor_data["conversation_id"]))
            after_clauses: list[dict[str, Any]] = [
                {
                    "$and": [
                        {"_pinned": cursor_pinned},
                        {"_activity_at": {"$lt": activity_at}},
                    ]
                },
                {
                    "$and": [
                        {"_pinned": cursor_pinned},
                        {"_activity_at": activity_at},
                        {"_id": {"$gt": conversation_id}},
                    ]
                },
            ]
            # Everything unpinned sorts after any pinned row, regardless of activity.
            if cursor_pinned:
                after_clauses.append({"_pinned": False})
            pipeline.append({"$match": {"$or": after_clauses}})

        pipeline.extend(
            [
                {"$sort": {"_pinned": -1, "_activity_at": -1, "_id": 1}},
                {"$limit": limit + 1},
            ]
        )

        rows = await (await ConversationDocument.get_pymongo_collection().aggregate(pipeline)).to_list(
            length=limit + 1
        )

        next_cursor: str | None = None
        if len(rows) > limit:
            last_visible = rows[limit - 1]
            next_cursor = encode_cursor(
                pinned=bool(last_visible.get("_pinned", False)),
                activity_at=last_visible["_activity_at"],
                conversation_id=str(last_visible["_id"]),
            )
            rows = rows[:limit]

        return [ConversationDocument.model_validate(row) for row in rows], next_cursor

    async def get_conversation_message(
        self, *, conversation_id: str, message_id: str
    ) -> MessageDocument | None:
        """Fetch a message and confirm it belongs to the given conversation."""
        message = await MessageDocument.get(parse_object_id(message_id))
        if message is None or str(message.conversation_id) != str(conversation_id):
            return None
        return message

    async def accessible_conversation_ids(self, *, user_id: str) -> list[str]:
        """Ids of every conversation the user participates in (unbounded)."""
        raw = await self.raw.find(
            {"participant_ids": str(user_id)}, {"_id": 1}
        ).to_list(length=None)
        return [str(item["_id"]) for item in raw]

    async def get_for_participant(
        self, *, conversation_id: str, user_id: str
    ) -> ConversationDocument | None:
        conversation = await self.get_by_id(conversation_id)
        if conversation is None:
            return None
        if str(user_id) not in [str(pid) for pid in conversation.participant_ids]:
            return None
        return conversation
