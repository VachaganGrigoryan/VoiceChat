from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pymongo import ReturnDocument

from app.db.models import (
    NotificationDocument,
    ParticipantDocument,
    PushTokenDocument,
    UserDocument,
)
from app.db.repository import BaseRepository
from app.modules.notifications.schemas import NotificationLevel, PushPlatform


class NotificationsRepository(BaseRepository[NotificationDocument]):
    model = NotificationDocument

    async def list_conversation_participants(
        self, *, conversation_id: str
    ) -> list[ParticipantDocument]:
        return await ParticipantDocument.find(
            {"conversation_id": str(conversation_id), "hidden": {"$ne": True}}
        ).to_list()

    async def users_by_ids(self, user_ids: list[str]) -> dict[str, UserDocument]:
        from app.modules.auth.repository import UsersRepository

        return await UsersRepository().find_by_ids(user_ids)

    async def create_notification(
        self,
        *,
        user_id: str,
        kind: str,
        source_type: str | None,
        source_id: str | None,
        conversation_id: str | None,
        data: dict[str, Any],
    ) -> NotificationDocument:
        now = datetime.now(UTC)
        notification = NotificationDocument(
            user_id=str(user_id),
            kind=kind,
            source_type=source_type,
            source_id=source_id,
            conversation_id=conversation_id,
            data=data,
            created_at=now,
            updated_at=now,
        )
        await notification.insert()
        return notification

    async def list_notifications(
        self, *, user_id: str, limit: int
    ) -> list[NotificationDocument]:
        return (
            await NotificationDocument.find({"user_id": str(user_id)})
            .sort("-created_at")
            .limit(limit)
            .to_list()
        )

    async def update_participant_settings(
        self,
        *,
        conversation_id: str,
        user_id: str,
        notification_level: NotificationLevel | None,
        muted_until: datetime | None,
        set_muted_until: bool,
    ) -> ParticipantDocument | None:
        updates: dict[str, Any] = {"updated_at": datetime.now(UTC)}
        if notification_level is not None:
            updates["notification_level"] = notification_level
            updates["muted"] = notification_level == "none"
        if set_muted_until:
            updates["muted_until"] = muted_until

        raw = await ParticipantDocument.get_pymongo_collection().find_one_and_update(
            {"conversation_id": str(conversation_id), "user_id": str(user_id)},
            {"$set": updates},
            return_document=ReturnDocument.AFTER,
        )
        return ParticipantDocument.model_validate(raw) if raw is not None else None

    async def update_user_preferences(
        self,
        *,
        user_id: str,
        updates: dict[str, Any],
    ) -> UserDocument | None:
        updates["updated_at"] = datetime.now(UTC)
        raw = await UserDocument.get_pymongo_collection().find_one_and_update(
            {"_id": self._parse_user_id(user_id)},
            {"$set": updates},
            return_document=ReturnDocument.AFTER,
        )
        return UserDocument.model_validate(raw) if raw is not None else None

    async def upsert_push_token(
        self,
        *,
        user_id: str,
        device_id: str | None,
        platform: PushPlatform,
        token: str,
    ) -> PushTokenDocument:
        now = datetime.now(UTC)
        raw = await PushTokenDocument.get_pymongo_collection().find_one_and_update(
            {"token": token},
            {
                "$set": {
                    "user_id": str(user_id),
                    "device_id": device_id,
                    "platform": platform,
                    "token": token,
                    "updated_at": now,
                },
                "$setOnInsert": {"created_at": now},
            },
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
        return PushTokenDocument.model_validate(raw)

    async def remove_push_token(
        self, *, user_id: str, device_id: str | None, token: str | None
    ) -> int:
        query: dict[str, Any] = {"user_id": str(user_id)}
        if token is not None:
            query["token"] = token
        elif device_id is not None:
            query["device_id"] = device_id
        else:
            return 0

        result = await PushTokenDocument.get_pymongo_collection().delete_many(query)
        return int(result.deleted_count)

    async def push_tokens_for_user(self, *, user_id: str) -> list[PushTokenDocument]:
        return await PushTokenDocument.find({"user_id": str(user_id)}).to_list()

    async def prune_push_tokens(self, *, tokens: list[str]) -> int:
        if not tokens:
            return 0
        result = await PushTokenDocument.get_pymongo_collection().delete_many(
            {"token": {"$in": tokens}}
        )
        return int(result.deleted_count)

    def _parse_user_id(self, user_id: str) -> Any:
        from app.db.object_id import parse_object_id

        return parse_object_id(user_id, message="Invalid user id")
