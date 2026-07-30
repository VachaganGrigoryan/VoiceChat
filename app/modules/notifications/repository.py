from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pymongo import ReturnDocument

from app.db.models import (
    MessageContainerType,
    NotificationDocument,
    ParticipantDocument,
    PushTokenDocument,
    RelationshipDocument,
    UserDocument,
)
from app.db.models.notification import NotificationKind, NotificationResourceType
from app.db.object_id import parse_object_id
from app.db.repository import BaseRepository
from app.modules.relationships.compat import to_participant
from app.modules.notifications.schemas import NotificationLevel, PushPlatform


class NotificationsRepository(BaseRepository[NotificationDocument]):
    model = NotificationDocument

    async def list_notification_recipients(
        self, *, resource_type: MessageContainerType, resource_id: str
    ) -> list[ParticipantDocument]:
        kinds = ["membership"] if resource_type == "conversation" else [
            "follow",
            "membership",
        ]
        docs = await RelationshipDocument.find(
            {
                "kind": {"$in": kinds},
                "target_type": resource_type,
                "target_id": str(resource_id),
                "status": "active",
                "state.hidden": {"$ne": True},
            }
        ).to_list()
        return [to_participant(doc) for doc in docs]

    async def users_by_ids(self, user_ids: list[str]) -> dict[str, UserDocument]:
        from app.modules.auth.repository import UsersRepository

        return await UsersRepository().find_by_ids(user_ids)

    async def create_notification(
        self,
        *,
        user_id: str,
        kind: NotificationKind,
        actor_user_id: str,
        resource_type: NotificationResourceType,
        resource_id: str,
        message_id: str | None,
        data: dict[str, Any],
    ) -> NotificationDocument:
        now = datetime.now(UTC)
        notification = NotificationDocument(
            user_id=str(user_id),
            kind=kind,
            actor_user_id=actor_user_id,
            resource_type=resource_type,
            resource_id=resource_id,
            message_id=message_id,
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

    async def mark_notification_read(
        self, *, notification_id: str, user_id: str, read_at: datetime
    ) -> NotificationDocument | None:
        """Mark one notification read, scoped to its owner.

        The `user_id` is part of the filter rather than a separate check, so a
        notification belonging to someone else simply does not match.
        `$exists`/`None` guard keeps a repeat call from moving the timestamp.
        """
        return await self.find_one_and_update(
            {
                "_id": parse_object_id(notification_id),
                "user_id": str(user_id),
                "read_at": None,
            },
            {"$set": {"read_at": read_at}},
        )

    async def find_notification(
        self, *, notification_id: str, user_id: str
    ) -> NotificationDocument | None:
        return await NotificationDocument.find_one(
            {"_id": parse_object_id(notification_id), "user_id": str(user_id)}
        )

    async def mark_all_notifications_read(
        self, *, user_id: str, read_at: datetime
    ) -> int:
        result = await self.raw.update_many(
            {"user_id": str(user_id), "read_at": None},
            {"$set": {"read_at": read_at}},
        )
        return int(result.modified_count)

    async def count_unread_notifications(self, *, user_id: str) -> int:
        return await NotificationDocument.find(
            {"user_id": str(user_id), "read_at": None}
        ).count()

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
            updates["state.notification_level"] = notification_level
        if set_muted_until:
            updates["state.muted_until"] = muted_until

        raw = await RelationshipDocument.get_pymongo_collection().find_one_and_update(
            {
                "kind": "membership",
                "target_type": "conversation",
                "target_id": str(conversation_id),
                "user_id": str(user_id),
            },
            {"$set": updates},
            return_document=ReturnDocument.AFTER,
        )
        if raw is None:
            return None
        return to_participant(RelationshipDocument.model_validate(raw))

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
