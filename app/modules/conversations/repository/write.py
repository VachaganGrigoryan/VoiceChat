from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pymongo import ReturnDocument
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
        space_id: str | None = None,
        space_visibility: str | None = None,
    ) -> ConversationDocument:
        now = datetime.now(UTC)
        conversation = ConversationDocument(
            type="group",
            participant_ids=participant_ids,
            created_by=str(created_by),
            title=title,
            encryption="none",
            dm_key=None,
            space_id=parse_object_id(space_id) if space_id is not None else None,
            space_visibility=space_visibility,  # type: ignore[arg-type]
            created_at=now,
            updated_at=now,
        )
        await conversation.insert()
        return conversation

    async def create_channel(
        self,
        *,
        created_by: str,
        participant_ids: list[str],
        title: str,
        description: str | None,
        visibility: str,
        posting_policy: str,
        slug: str | None,
        read_policy: str = "members",
        space_id: str | None = None,
        space_visibility: str | None = None,
    ) -> ConversationDocument:
        """Create a ``channel`` conversation.

        Raises ``DuplicateKeyError`` when ``slug`` collides with an existing
        public conversation (partial-unique ``slug`` index); the caller maps
        that to a conflict error.
        """
        now = datetime.now(UTC)
        conversation = ConversationDocument(
            type="channel",
            participant_ids=participant_ids,
            created_by=str(created_by),
            title=title,
            description=description,
            visibility=visibility,  # type: ignore[arg-type]
            posting_policy=posting_policy,  # type: ignore[arg-type]
            read_policy=read_policy,  # type: ignore[arg-type]
            slug=slug,
            space_id=parse_object_id(space_id) if space_id is not None else None,
            space_visibility=space_visibility,  # type: ignore[arg-type]
            member_count=len(participant_ids),
            encryption="none",
            dm_key=None,
            created_at=now,
            updated_at=now,
        )
        await conversation.insert()
        return conversation

    async def get_public_by_slug(self, slug: str) -> ConversationDocument | None:
        return await ConversationDocument.find_one(
            {"slug": slug, "visibility": "public"}
        )

    async def ensure_thread(
        self, *, parent_conversation_id: str, root_message_id: str, created_by: str
    ) -> ConversationDocument:
        """Create-or-get the ``thread`` sub-conversation for a root message."""
        existing = await ConversationDocument.find_one(
            {
                "type": "thread",
                "parent_conversation_id": str(parent_conversation_id),
                "root_message_id": str(root_message_id),
            }
        )
        if existing is not None:
            return existing

        now = datetime.now(UTC)
        conversation = ConversationDocument(
            type="thread",
            participant_ids=[str(created_by)],
            created_by=str(created_by),
            parent_conversation_id=str(parent_conversation_id),
            root_message_id=str(root_message_id),
            member_count=1,
            encryption="none",
            dm_key=None,
            created_at=now,
            updated_at=now,
        )
        await conversation.insert()
        return conversation

    async def lock_thread_after_conversion(
        self, *, thread_id: str, user_id: str, group_id: str
    ) -> ConversationDocument | None:
        now = datetime.now(UTC)
        raw = await self.raw.find_one_and_update(
            {
                "_id": parse_object_id(thread_id),
                "type": "thread",
                "created_by": str(user_id),
            },
            {
                "$set": {
                    "settings.locked_at": now,
                    "settings.locked_by": str(user_id),
                    "settings.converted_to_conversation_id": str(group_id),
                    "updated_at": now,
                }
            },
            return_document=ReturnDocument.AFTER,
        )
        return ConversationDocument.model_validate(raw) if raw is not None else None

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

    async def set_conversation_setting(
        self, *, conversation_id: str, key: str, value: Any
    ) -> ConversationDocument:
        now = datetime.now(UTC)
        await self.raw.update_one(
            {"_id": parse_object_id(conversation_id)},
            {"$set": {f"settings.{key}": value, "updated_at": now}},
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

    async def sync_member_count(self, *, conversation_id: str) -> None:
        """Recompute denormalized ``member_count`` from ``participant_ids``."""
        now = datetime.now(UTC)
        await self.raw.update_one(
            {"_id": parse_object_id(conversation_id)},
            [
                {
                    "$set": {
                        "member_count": {"$size": "$participant_ids"},
                        "updated_at": now,
                    }
                }
            ],
        )

    async def add_pinned_message(
        self, *, conversation_id: str, message_id: str
    ) -> ConversationDocument | None:
        now = datetime.now(UTC)
        raw = await self.raw.find_one_and_update(
            {"_id": parse_object_id(conversation_id)},
            {
                "$addToSet": {"pinned_message_ids": str(message_id)},
                "$set": {"updated_at": now},
            },
            return_document=ReturnDocument.AFTER,
        )
        return ConversationDocument.model_validate(raw) if raw is not None else None

    async def remove_pinned_message(
        self, *, conversation_id: str, message_id: str
    ) -> ConversationDocument | None:
        now = datetime.now(UTC)
        raw = await self.raw.find_one_and_update(
            {"_id": parse_object_id(conversation_id)},
            {
                "$pull": {"pinned_message_ids": str(message_id)},
                "$set": {"updated_at": now},
            },
            return_document=ReturnDocument.AFTER,
        )
        return ConversationDocument.model_validate(raw) if raw is not None else None

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
