from __future__ import annotations

from datetime import UTC, datetime

from pymongo.errors import DuplicateKeyError

from app.db.models import ConversationDocument, ConversationPreviewDocument
from app.db.object_id import parse_object_id
from app.modules.conversations.repository.helpers import dm_key_for


class ConversationsWriteMixin:
    async def ensure_dm(
        self, *, user_a: str, user_b: str, created_by: str
    ) -> ConversationDocument:
        """Create-or-get the DM conversation for a user pair.

        Race-safe: the unique ``dm_key`` index rejects a duplicate insert, and the
        loser re-reads the winner's document.
        """
        dm_key = dm_key_for(user_a, user_b)
        existing = await self.get_by_dm_key(dm_key)
        if existing is not None:
            return existing

        now = datetime.now(UTC)
        conversation = ConversationDocument(
            type="dm",
            participant_ids=sorted([str(user_a), str(user_b)]),
            created_by=str(created_by),
            dm_key=dm_key,
            created_at=now,
            updated_at=now,
        )
        try:
            await conversation.insert()
        except DuplicateKeyError:
            existing = await self.get_by_dm_key(dm_key)
            if existing is None:  # pragma: no cover - index guarantees a winner
                raise
            return existing
        return conversation

    async def get_by_dm_key(self, dm_key: str) -> ConversationDocument | None:
        return await ConversationDocument.find_one({"type": "dm", "dm_key": dm_key})

    async def create_group(
        self,
        *,
        created_by: str,
        participant_ids: list[str],
        title: str,
    ) -> ConversationDocument:
        now = datetime.now(UTC)
        conversation = ConversationDocument(
            type="group",
            participant_ids=participant_ids,
            created_by=str(created_by),
            title=title,
            encryption="none",
            dm_key=None,
            created_at=now,
            updated_at=now,
        )
        await conversation.insert()
        return conversation

    async def update_group_title(
        self, *, conversation_id: str, title: str
    ) -> ConversationDocument:
        now = datetime.now(UTC)
        await self.raw.update_one(
            {"_id": parse_object_id(conversation_id)},
            {"$set": {"title": title, "updated_at": now}},
        )
        updated = await self.get_by_id(conversation_id)
        assert updated is not None
        return updated

    async def update_group_image(
        self, *, conversation_id: str, image: dict | None
    ) -> ConversationDocument:
        now = datetime.now(UTC)
        await self.raw.update_one(
            {"_id": parse_object_id(conversation_id)},
            {"$set": {"image": image, "updated_at": now}},
        )
        updated = await self.get_by_id(conversation_id)
        assert updated is not None
        return updated

    async def delete_conversation(self, *, conversation_id: str) -> None:
        await self.raw.delete_one({"_id": parse_object_id(conversation_id)})

    async def add_participant_id(
        self, *, conversation_id: str, user_id: str
    ) -> None:
        now = datetime.now(UTC)
        await self.raw.update_one(
            {"_id": parse_object_id(conversation_id)},
            {
                "$addToSet": {"participant_ids": str(user_id)},
                "$set": {"updated_at": now},
            },
        )

    async def remove_participant_id(
        self, *, conversation_id: str, user_id: str
    ) -> None:
        now = datetime.now(UTC)
        await self.raw.update_one(
            {"_id": parse_object_id(conversation_id)},
            {
                "$pull": {"participant_ids": str(user_id)},
                "$set": {"updated_at": now},
            },
        )

    async def touch_last_message(
        self,
        *,
        conversation_id: str,
        preview: ConversationPreviewDocument,
    ) -> None:
        """Advance ``last_message_at`` / ``last_message_preview`` for the inbox."""
        now = datetime.now(UTC)
        await self.raw.update_one(
            {"_id": parse_object_id(conversation_id)},
            {
                "$set": {
                    "last_message_at": preview.created_at,
                    "last_message_preview": preview.model_dump(mode="python"),
                    "updated_at": now,
                }
            },
        )
