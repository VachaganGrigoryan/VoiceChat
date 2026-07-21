from __future__ import annotations

from typing import Any

from app.core.pagination.cursor import decode_cursor, encode_cursor
from app.db.models import ConversationDocument
from app.db.object_id import parse_object_id


class ConversationsReadMixin:
    async def list_for_user(
        self,
        *,
        user_id: str,
        limit: int,
        cursor: str | None = None,
    ) -> tuple[list[ConversationDocument], str | None]:
        """List a user's conversations, most-recent activity first.

        Cursor is the activity timestamp plus conversation id of the last row seen.
        """
        pipeline: list[dict[str, Any]] = [
            {"$match": {"participant_ids": str(user_id)}},
            {"$addFields": {"_activity_at": {"$ifNull": ["$last_message_at", "$updated_at"]}}},
        ]

        if cursor is not None:
            cursor_data = decode_cursor(
                cursor, required_fields={"activity_at", "conversation_id"}
            )
            activity_at = cursor_data["activity_at"]
            conversation_id = parse_object_id(str(cursor_data["conversation_id"]))
            pipeline.append(
                {
                    "$match": {
                        "$or": [
                            {"_activity_at": {"$lt": activity_at}},
                            {
                                "$and": [
                                    {"_activity_at": activity_at},
                                    {"_id": {"$gt": conversation_id}},
                                ]
                            },
                        ]
                    }
                }
            )

        pipeline.extend(
            [
                {"$sort": {"_activity_at": -1, "_id": 1}},
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
                activity_at=last_visible["_activity_at"],
                conversation_id=str(last_visible["_id"]),
            )
            rows = rows[:limit]

        return [ConversationDocument.model_validate(row) for row in rows], next_cursor

    async def get_for_participant(
        self, *, conversation_id: str, user_id: str
    ) -> ConversationDocument | None:
        conversation = await self.get_by_id(conversation_id)
        if conversation is None:
            return None
        if str(user_id) not in [str(pid) for pid in conversation.participant_ids]:
            return None
        return conversation
