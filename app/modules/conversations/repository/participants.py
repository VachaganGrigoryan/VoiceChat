from __future__ import annotations

from datetime import UTC, datetime

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from app.db.models import MessageDocument, ParticipantDocument


class ParticipantsRepositoryMixin:
    async def ensure_participant(
        self, *, conversation_id: str, user_id: str, role: str = "member"
    ) -> ParticipantDocument:
        existing = await ParticipantDocument.find_one(
            {"conversation_id": str(conversation_id), "user_id": str(user_id)}
        )
        if existing is not None:
            return existing

        now = datetime.now(UTC)
        participant = ParticipantDocument(
            conversation_id=str(conversation_id),
            user_id=str(user_id),
            role=role,
            joined_at=now,
            created_at=now,
            updated_at=now,
        )
        try:
            await participant.insert()
        except DuplicateKeyError:
            existing = await ParticipantDocument.find_one(
                {"conversation_id": str(conversation_id), "user_id": str(user_id)}
            )
            if existing is None:  # pragma: no cover - index guarantees a winner
                raise
            return existing
        return participant

    async def get_participant(
        self, *, conversation_id: str, user_id: str
    ) -> ParticipantDocument | None:
        return await ParticipantDocument.find_one(
            {"conversation_id": str(conversation_id), "user_id": str(user_id)}
        )

    async def list_participants(
        self, *, conversation_id: str
    ) -> list[ParticipantDocument]:
        return await ParticipantDocument.find(
            {"conversation_id": str(conversation_id)}
        ).to_list()

    async def set_participant_role(
        self, *, conversation_id: str, user_id: str, role: str
    ) -> ParticipantDocument | None:
        now = datetime.now(UTC)
        raw = await ParticipantDocument.get_pymongo_collection().find_one_and_update(
            {"conversation_id": str(conversation_id), "user_id": str(user_id)},
            {"$set": {"role": role, "updated_at": now}},
            return_document=ReturnDocument.AFTER,
        )
        return ParticipantDocument.model_validate(raw) if raw is not None else None

    async def set_participant_permissions(
        self, *, conversation_id: str, user_id: str, permissions: dict | None
    ) -> ParticipantDocument | None:
        now = datetime.now(UTC)
        raw = await ParticipantDocument.get_pymongo_collection().find_one_and_update(
            {"conversation_id": str(conversation_id), "user_id": str(user_id)},
            {"$set": {"permissions": permissions, "updated_at": now}},
            return_document=ReturnDocument.AFTER,
        )
        return ParticipantDocument.model_validate(raw) if raw is not None else None

    async def delete_participant(
        self, *, conversation_id: str, user_id: str
    ) -> bool:
        result = await ParticipantDocument.get_pymongo_collection().delete_one(
            {"conversation_id": str(conversation_id), "user_id": str(user_id)}
        )
        return result.deleted_count > 0

    async def update_participant_inbox_state(
        self, *, conversation_id: str, user_id: str, updates: dict
    ) -> ParticipantDocument | None:
        """Set the caller's ``pinned``/``archived``/``folder`` inbox flags."""
        set_fields = {
            key: updates[key]
            for key in ("pinned", "archived", "folder")
            if key in updates
        }
        set_fields["updated_at"] = datetime.now(UTC)
        raw = await ParticipantDocument.get_pymongo_collection().find_one_and_update(
            {"conversation_id": str(conversation_id), "user_id": str(user_id)},
            {"$set": set_fields},
            return_document=ReturnDocument.AFTER,
        )
        return ParticipantDocument.model_validate(raw) if raw is not None else None

    async def update_many_inbox_state(
        self, *, conversation_ids: list[str], user_id: str, updates: dict
    ) -> int:
        """Apply whitelisted inbox flags to several of the caller's rows at once.

        Returns the number of participant rows modified.
        """
        set_fields = {
            key: updates[key]
            for key in ("pinned", "archived", "folder")
            if key in updates
        }
        if not set_fields:
            return 0
        set_fields["updated_at"] = datetime.now(UTC)
        result = await ParticipantDocument.get_pymongo_collection().update_many(
            {
                "conversation_id": {"$in": [str(cid) for cid in conversation_ids]},
                "user_id": str(user_id),
            },
            {"$set": set_fields},
        )
        return result.modified_count

    async def clear_archived_if_set(
        self, *, conversation_id: str, user_id: str
    ) -> ParticipantDocument | None:
        """Unarchive the caller's row only if it is currently archived.

        Conditional so ordinary sends to non-archived conversations perform no
        write and don't churn ``updated_at``. Returns the updated row, or ``None``
        when nothing was archived.
        """
        raw = await ParticipantDocument.get_pymongo_collection().find_one_and_update(
            {
                "conversation_id": str(conversation_id),
                "user_id": str(user_id),
                "archived": True,
            },
            {"$set": {"archived": False, "updated_at": datetime.now(UTC)}},
            return_document=ReturnDocument.AFTER,
        )
        return ParticipantDocument.model_validate(raw) if raw is not None else None

    async def aggregate_folders(self, *, user_id: str) -> list[dict]:
        """Return the caller's folders with total and archived conversation counts.

        Discovered from the per-participant ``folder`` label, so folders survive
        even when all their conversations are archived or past a listing page.
        """
        cursor = await ParticipantDocument.get_pymongo_collection().aggregate(
            [
                {"$match": {"user_id": str(user_id), "folder": {"$ne": None}}},
                {
                    "$group": {
                        "_id": "$folder",
                        "count": {"$sum": 1},
                        "archived_count": {
                            "$sum": {"$cond": ["$archived", 1, 0]},
                        },
                    }
                },
                {"$sort": {"_id": 1}},
            ]
        )
        return [
            {
                "name": row["_id"],
                "count": row["count"],
                "archived_count": row["archived_count"],
            }
            async for row in cursor
        ]

    async def rename_folder(
        self, *, user_id: str, old_name: str, new_name: str
    ) -> int:
        """Rename a folder across all the caller's conversations."""
        result = await ParticipantDocument.get_pymongo_collection().update_many(
            {"user_id": str(user_id), "folder": old_name},
            {"$set": {"folder": new_name, "updated_at": datetime.now(UTC)}},
        )
        return result.modified_count

    async def clear_folder(self, *, user_id: str, name: str) -> int:
        """Remove a folder label from all the caller's conversations."""
        result = await ParticipantDocument.get_pymongo_collection().update_many(
            {"user_id": str(user_id), "folder": name},
            {"$set": {"folder": None, "updated_at": datetime.now(UTC)}},
        )
        return result.modified_count

    async def set_participant_draft(
        self,
        *,
        conversation_id: str,
        user_id: str,
        draft_text: str | None,
        draft_updated_at: datetime | None,
    ) -> ParticipantDocument | None:
        """Set or clear the caller's per-conversation draft."""
        raw = await ParticipantDocument.get_pymongo_collection().find_one_and_update(
            {"conversation_id": str(conversation_id), "user_id": str(user_id)},
            {
                "$set": {
                    "draft_text": draft_text,
                    "draft_updated_at": draft_updated_at,
                    "updated_at": datetime.now(UTC),
                }
            },
            return_document=ReturnDocument.AFTER,
        )
        return ParticipantDocument.model_validate(raw) if raw is not None else None

    async def mark_read(
        self,
        *,
        conversation_id: str,
        user_id: str,
        last_read_message_id: str | None,
        last_read_at: datetime | None = None,
    ) -> None:
        now = datetime.now(UTC)
        await ParticipantDocument.get_pymongo_collection().update_one(
            {"conversation_id": str(conversation_id), "user_id": str(user_id)},
            {
                "$set": {
                    "last_read_at": last_read_at or now,
                    "last_read_message_id": last_read_message_id,
                    "updated_at": now,
                }
            },
        )

    async def unread_count(
        self,
        *,
        message_conversation_id: str,
        user_id: str,
        last_read_at: datetime | None,
    ) -> int:
        """Count messages from other senders newer than the user's read cursor."""
        query: dict = {
            "conversation_id": str(message_conversation_id),
            "sender_id": {"$ne": str(user_id)},
            "thread_root_id": None,
            "state": {"$ne": "scheduled"},
        }
        if last_read_at is not None:
            query["created_at"] = {"$gt": last_read_at}
        return await MessageDocument.find(query).count()
