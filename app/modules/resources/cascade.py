"""Hard deletion of a resource and everything that named it.

One routine per resource type, with the space routine calling the other two for
each child it owns. Every delete in the system that removes a container goes
through here, so the set of collections that must be cleared is stated once.

**Ordering is the design, not an implementation detail.** Nothing in this
codebase opens a Mongo session or a transaction, so a cascade spanning ten
collections cannot be atomic. Two rules follow:

1. The realtime audience is snapshotted *before* anything is deleted. Both the
   participant list and `affected_viewer_ids` read `relationships`, which this
   module is about to empty; reading them afterwards would emit to nobody.
2. Children are deleted before parents and the parent record **last**. An
   interruption then leaves a resource that still resolves an owner and can be
   deleted again. The opposite order strands children under a missing parent,
   and a space-owned child whose space is gone resolves no owner at all — the
   one failure the ownership model cannot recover from.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from app.db.models import (
    CallDocument,
    ChannelDocument,
    ConversationDocument,
    InviteLinkDocument,
    MessageDocument,
    MessageReceiptDocument,
    NotificationDocument,
    PollDocument,
    RelationshipDocument,
    ReportDocument,
    SavedMessageDocument,
    SpaceDocument,
    UserDocument,
    WebhookDocument,
)
from app.infra.storage import get_storage
from app.modules.authorization.repository import RolesRepository

logger = logging.getLogger(__name__)


@dataclass
class CascadeReport:
    """What a cascade removed, per collection, for the audit record."""

    removed: dict[str, int] = field(default_factory=dict)

    def add(self, collection: str, count: int) -> None:
        if count:
            self.removed[collection] = self.removed.get(collection, 0) + count

    def merge(self, other: "CascadeReport") -> None:
        for collection, count in other.removed.items():
            self.add(collection, count)


class ResourceCascade:
    """Deletes a conversation, a channel or a space with everything it owned."""

    def __init__(self, roles_repo: RolesRepository | None = None) -> None:
        self.roles = roles_repo or RolesRepository()

    # --- helpers ------------------------------------------------------------

    @staticmethod
    async def _delete_many(document: type, query: dict) -> int:
        result = await document.get_pymongo_collection().delete_many(query)
        return int(result.deleted_count)

    @staticmethod
    async def _delete_blob(media: dict | None) -> None:
        """Storage failures must not abort a cascade.

        An unreferenced object left in storage is wasteful; a half-deleted
        resource is not recoverable. The document deletions win.
        """
        if not media:
            return
        key = media.get("key")
        provider = media.get("storage")
        if not key or not provider:
            return
        try:
            await get_storage(provider).delete(key)
        except Exception:  # noqa: BLE001 - storage is best-effort here
            logger.warning("cascade: could not delete blob %s/%s", provider, key)

    async def _delete_container_messages(
        self, *, container_type: str, container_id: str, report: CascadeReport
    ) -> list[str]:
        """Removes a container's messages, their blobs, and what keyed off them."""
        collection = MessageDocument.get_pymongo_collection()
        cursor = collection.find(
            {"container_type": container_type, "container_id": str(container_id)},
            {"_id": 1, "content": 1},
        )

        message_ids: list[str] = []
        async for raw in cursor:
            message_ids.append(str(raw["_id"]))
            content = raw.get("content") or {}
            plaintext = content.get("plaintext") or {}
            await self._delete_blob(plaintext.get("media"))
            for attachment in content.get("attachments") or []:
                await self._delete_blob(attachment)

        if not message_ids:
            return []

        report.add(
            "messages",
            await self._delete_many(
                MessageDocument,
                {"container_type": container_type, "container_id": str(container_id)},
            ),
        )
        # Keyed by the messages rather than by the container, so they can only be
        # found while the message ids are still in hand.
        report.add(
            "saved_messages",
            await self._delete_many(
                SavedMessageDocument, {"message_id": {"$in": message_ids}}
            ),
        )
        report.add(
            "polls",
            await self._delete_many(PollDocument, {"message_id": {"$in": message_ids}}),
        )
        report.add(
            "notifications",
            await self._delete_many(
                NotificationDocument, {"message_id": {"$in": message_ids}}
            ),
        )
        return message_ids

    async def _delete_shared_scope(
        self, *, resource_type: str, resource_id: str, report: CascadeReport
    ) -> None:
        """The records every resource type carries, under its own scope."""
        report.add(
            "relationships",
            await self._delete_many(
                RelationshipDocument,
                {"target_type": resource_type, "target_id": str(resource_id)},
            ),
        )
        # Reused rather than a raw delete: it also evicts the role-name cache.
        report.add(
            "roles",
            await self.roles.delete_for_scope(
                scope_type=resource_type, scope_id=str(resource_id)
            ),
        )
        report.add(
            "invite_links",
            await self._delete_many(
                InviteLinkDocument,
                {"target_type": resource_type, "target_id": str(resource_id)},
            ),
        )
        report.add(
            "notifications",
            await self._delete_many(
                NotificationDocument,
                {"resource_type": resource_type, "resource_id": str(resource_id)},
            ),
        )
        report.add(
            "webhooks",
            await self._delete_many(
                WebhookDocument,
                {"target_type": resource_type, "target_id": str(resource_id)},
            ),
        )
        report.add(
            "reports",
            await self._delete_many(
                ReportDocument,
                {"target_type": resource_type, "target_id": str(resource_id)},
            ),
        )

    # --- entry points -------------------------------------------------------

    async def delete_conversation_tree(
        self, conversation: ConversationDocument
    ) -> CascadeReport:
        report = CascadeReport()
        conversation_id = conversation.str_id

        await self._delete_container_messages(
            container_type="conversation", container_id=conversation_id, report=report
        )
        report.add(
            "message_receipts",
            await self._delete_many(
                MessageReceiptDocument, {"conversation_id": conversation_id}
            ),
        )
        report.add(
            "calls",
            await self._delete_many(CallDocument, {"conversation_id": conversation_id}),
        )
        await self._delete_shared_scope(
            resource_type="conversation", resource_id=conversation_id, report=report
        )

        # Only cleaned on replace until now, so a deleted group leaked its avatar.
        image = conversation.image
        await self._delete_blob(image.model_dump() if image is not None else None)

        await conversation.delete()
        report.add("conversations", 1)
        return report

    async def delete_channel_tree(self, channel: ChannelDocument) -> CascadeReport:
        report = CascadeReport()
        channel_id = channel.str_id

        await self._delete_container_messages(
            container_type="channel", container_id=channel_id, report=report
        )
        # `relationships` covers memberships and follows alike; a channel has no
        # receipts and no join requests of its own.
        await self._delete_shared_scope(
            resource_type="channel", resource_id=channel_id, report=report
        )

        for media in (channel.avatar, channel.banner):
            await self._delete_blob(media.model_dump() if media is not None else None)

        # A dangling main channel would leave the profile feed pointing at nothing.
        cleared = await UserDocument.get_pymongo_collection().update_many(
            {"main_channel_id": channel_id}, {"$set": {"main_channel_id": None}}
        )
        report.add("users.main_channel_id", int(cleared.modified_count))

        await channel.delete()
        report.add("channels", 1)
        return report

    async def delete_space_tree(self, space: SpaceDocument) -> CascadeReport:
        """Children first, then the space's own scope, then the space.

        Cascading is not optional: a channel or group owned by a space resolves
        its owner through that space, so one left behind would resolve no owner
        and could never be managed or deleted again.
        """
        report = CascadeReport()
        space_id = space.str_id

        channels = await ChannelDocument.find(
            ChannelDocument.space_id == space_id
        ).to_list()
        for channel in channels:
            report.merge(await self.delete_channel_tree(channel))

        groups = await ConversationDocument.find(
            ConversationDocument.space_id == space_id
        ).to_list()
        for group in groups:
            report.merge(await self.delete_conversation_tree(group))

        await self._delete_shared_scope(
            resource_type="space", resource_id=space_id, report=report
        )

        await space.delete()
        report.add("spaces", 1)
        return report
