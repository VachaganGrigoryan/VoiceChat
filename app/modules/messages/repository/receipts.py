from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.db.models import (
    MessageContainerType,
    MessageReceiptDocument,
    RelationshipDocument,
)
from app.modules.messages.schemas import MessageReceiptSummary


class ReceiptsRepositoryMixin:
    """Delivery/read receipts.

    Receipts count against a conversation's participants, so they are only
    meaningful for a conversation container; a channel has followers rather than
    a recipient roster and reports empty summaries.
    """

    async def upsert_message_receipt(
        self,
        *,
        container_type: MessageContainerType,
        container_id: str,
        message_id: str,
        user_id: str,
        delivered: bool = False,
        read: bool = False,
    ) -> MessageReceiptSummary:
        if container_type != "conversation":
            return MessageReceiptSummary()

        now = datetime.now(UTC)
        set_data: dict[str, Any] = {"updated_at": now}
        set_on_insert = {
            "conversation_id": container_id,
            "message_id": message_id,
            "user_id": user_id,
            "created_at": now,
        }
        if delivered or read:
            set_data["delivered_at"] = now
        if read:
            set_data["read_at"] = now

        await MessageReceiptDocument.get_pymongo_collection().update_one(
            {"message_id": message_id, "user_id": user_id},
            {"$set": set_data, "$setOnInsert": set_on_insert},
            upsert=True,
        )
        return (
            await self.receipt_summaries_for_messages(
                container_type="conversation",
                container_id=container_id,
                messages=[await self.get_by_id(message_id=message_id)],
            )
        )[message_id]

    async def receipt_summaries_for_messages(
        self,
        *,
        container_type: MessageContainerType,
        container_id: str,
        messages: list[Any],
    ) -> dict[str, MessageReceiptSummary]:
        visible_messages = [message for message in messages if message is not None]
        if not visible_messages or container_type != "conversation":
            return {}
        conversation_id = container_id

        participant_docs = await RelationshipDocument.find(
            {
                "kind": "membership",
                "target_type": "conversation",
                "target_id": conversation_id,
                "status": "active",
                "state.hidden": {"$ne": True},
            }
        ).to_list()
        participant_ids = {
            str(participant.user_id) for participant in participant_docs
        }
        message_ids = [message.str_id for message in visible_messages]
        sender_by_message = {
            message.str_id: str(message.sender_id) for message in visible_messages
        }

        cursor = await MessageReceiptDocument.get_pymongo_collection().aggregate(
            [
                {
                    "$match": {
                        "conversation_id": conversation_id,
                        "message_id": {"$in": message_ids},
                    }
                },
                {
                    "$group": {
                        "_id": "$message_id",
                        "delivered_count": {
                            "$sum": {"$cond": [{"$ne": ["$delivered_at", None]}, 1, 0]}
                        },
                        "read_count": {
                            "$sum": {"$cond": [{"$ne": ["$read_at", None]}, 1, 0]}
                        },
                    }
                },
            ]
        )
        rows = await cursor.to_list(length=None)
        counts_by_message = {str(row["_id"]): row for row in rows}

        summaries: dict[str, MessageReceiptSummary] = {}
        for message_id in message_ids:
            recipient_count = max(
                len(participant_ids - {sender_by_message[message_id]}),
                0,
            )
            counts = counts_by_message.get(message_id, {})
            summaries[message_id] = MessageReceiptSummary(
                recipient_count=recipient_count,
                delivered_count=int(counts.get("delivered_count", 0)),
                read_count=int(counts.get("read_count", 0)),
            )
        return summaries
