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
