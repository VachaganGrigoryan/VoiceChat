from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from beanie.operators import In
from pymongo import DESCENDING, ReturnDocument
from pymongo.errors import DuplicateKeyError

from app.core.errors import AppError
from app.db.models import ChannelDocument
from app.db.object_id import parse_object_id
from app.db.repository import BaseRepository


class ChannelsRepository(BaseRepository[ChannelDocument]):
    model = ChannelDocument

    async def get_by_owner_slug(
        self, *, owner_type: str, owner_id: str, slug: str
    ) -> ChannelDocument | None:
        return await ChannelDocument.find_one(
            {
                "owner.type": owner_type,
                "owner.id": str(owner_id),
                "slug": slug,
            }
        )

    async def get_profile_channel(self, *, user_id: str) -> ChannelDocument | None:
        return await ChannelDocument.find_one(
            {
                "owner.type": "user",
                "owner.id": str(user_id),
                "kind": "profile",
            }
        )

    async def list_by_ids(self, channel_ids: list[str]) -> list[ChannelDocument]:
        if not channel_ids:
            return []
        unique_ids = list(dict.fromkeys(channel_ids))
        object_ids = [parse_object_id(channel_id) for channel_id in unique_ids]
        return await ChannelDocument.find(In(ChannelDocument.id, object_ids)).to_list()

    async def insert(self, channel: ChannelDocument) -> ChannelDocument:
        try:
            await channel.insert()
        except DuplicateKeyError as exc:
            raise AppError(
                code="CHANNEL_SLUG_TAKEN",
                message="A channel with this slug already exists for this owner",
                status_code=409,
            ) from exc
        return channel

    async def update_by_id(
        self, *, channel_id: str, updates: dict[str, Any]
    ) -> ChannelDocument | None:
        channel = await self.get_by_id(channel_id, invalid_message="Invalid channel id")
        if channel is None:
            return None
        return await self.find_one_and_update(
            {"_id": channel.id},
            {"$set": {**updates, "updated_at": datetime.now(UTC)}},
        )

    async def record_message(
        self,
        *,
        channel_id: str,
        message_id: str,
        created_at: datetime,
    ) -> ChannelDocument | None:
        channel = await self.get_by_id(channel_id, invalid_message="Invalid channel id")
        if channel is None:
            return None
        raw = await self.raw.find_one_and_update(
            {"_id": channel.id},
            {
                "$inc": {"message_count": 1},
                "$set": {
                    "last_message_id": str(message_id),
                    "last_activity_at": created_at,
                    "updated_at": datetime.now(UTC),
                },
            },
            return_document=ReturnDocument.AFTER,
        )
        return ChannelDocument.model_validate(raw) if raw is not None else None

    async def adjust_follower_count(
        self, *, channel_id: str, delta: int
    ) -> ChannelDocument | None:
        channel = await self.get_by_id(channel_id, invalid_message="Invalid channel id")
        if channel is None:
            return None
        query: dict[str, Any] = {"_id": channel.id}
        if delta < 0:
            query["follower_count"] = {"$gte": abs(delta)}
        raw = await self.raw.find_one_and_update(
            query,
            {
                "$inc": {"follower_count": delta},
                "$set": {"updated_at": datetime.now(UTC)},
            },
            return_document=ReturnDocument.AFTER,
        )
        return ChannelDocument.model_validate(raw) if raw is not None else channel

    async def list_by_owner(
        self, *, owner_type: str, owner_id: str, limit: int, skip: int = 0
    ) -> list[ChannelDocument]:
        return (
            await ChannelDocument.find(
                {"owner.type": owner_type, "owner.id": str(owner_id)}
            )
            .sort([("created_at", DESCENDING)])
            .skip(skip)
            .limit(limit)
            .to_list()
        )
