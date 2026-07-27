from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, time
from typing import Any, Protocol
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.core.errors import AppError
from app.db.models.notification import NotificationKind, NotificationResourceType
from app.infra.queue.base import JobQueue
from app.modules.messages.schemas import MessageDoc
from app.modules.notifications.repository import NotificationsRepository
from app.modules.notifications.schemas import (
    NotificationPreferencesRequest,
    NotificationView,
    PushTokenRegisterRequest,
    PushTokenView,
)

PUSH_QUEUE_NAME = "push.deliver"


class UserProfileProto(Protocol):
    id: str


@dataclass(frozen=True)
class GeneratedNotification:
    notification: NotificationView
    push_token_count: int
    push_suppressed_by_dnd: bool


class NotificationsService:
    def __init__(
        self,
        repo: NotificationsRepository,
        queue: JobQueue | None = None,
        *,
        push_queue_name: str = PUSH_QUEUE_NAME,
    ) -> None:
        self.repo = repo
        self.queue = queue
        self.push_queue_name = push_queue_name

    async def generate_for_message(
        self, *, message: MessageDoc
    ) -> list[GeneratedNotification]:
        participants = await self.repo.list_notification_recipients(
            resource_type=message.container_type,
            resource_id=message.container_id,
        )
        user_ids = [str(participant.user_id) for participant in participants]
        users = await self.repo.users_by_ids(user_ids)
        text = message.content.plaintext.text if message.content and message.content.plaintext else ""
        now = datetime.now(UTC)
        generated: list[GeneratedNotification] = []

        for participant in participants:
            user_id = str(participant.user_id)
            if user_id == message.sender_id:
                continue
            if self._is_muted(participant.muted_until, now):
                continue

            user = users.get(user_id)
            keyword = self._matched_keyword(
                text=text,
                keywords=getattr(user, "notification_keywords", []) if user else [],
            )
            mentioned = self._is_mentioned(message=message, user_id=user_id)
            should_notify = (
                participant.notification_level == "all"
                or (participant.notification_level == "mentions" and mentioned)
                or keyword is not None
                or (participant.notification_level == "none" and keyword is not None)
            )
            if not should_notify:
                continue

            push_suppressed_by_dnd = self._is_in_dnd_window(user, now)
            tokens = await self.repo.push_tokens_for_user(user_id=user_id)
            notification = await self.repo.create_notification(
                user_id=user_id,
                kind="mention" if mentioned else "message",
                actor_user_id=str(message.sender_id),
                resource_type=message.container_type,
                resource_id=message.container_id,
                message_id=str(message.id),
                data={
                    "sender_id": message.sender_id,
                    "message_type": message.type,
                    "mention": mentioned,
                    "mention_scope": message.mention_scope,
                    "keyword": keyword,
                    "push_suppressed_by_dnd": push_suppressed_by_dnd,
                },
            )
            view = self._to_notification_view(notification)
            if tokens and not push_suppressed_by_dnd:
                await self._enqueue_push(
                    notification=view,
                    tokens=[token.token for token in tokens],
                )
            generated.append(
                GeneratedNotification(
                    notification=view,
                    push_token_count=len(tokens),
                    push_suppressed_by_dnd=push_suppressed_by_dnd,
                )
            )

        return generated

    async def create_notification(
        self,
        *,
        user_id: str,
        kind: NotificationKind,
        actor_user_id: str,
        resource_type: NotificationResourceType,
        resource_id: str,
        message_id: str | None = None,
        data: dict[str, Any] | None = None,
    ) -> NotificationView:
        notification = await self.repo.create_notification(
            user_id=user_id,
            kind=kind,
            actor_user_id=actor_user_id,
            resource_type=resource_type,
            resource_id=resource_id,
            message_id=message_id,
            data=data or {},
        )
        return self._to_notification_view(notification)

    async def list_for_user(self, *, user_id: str, limit: int) -> list[NotificationView]:
        if limit < 1 or limit > 100:
            raise AppError(
                code="INVALID_LIMIT",
                message="limit must be between 1 and 100",
                status_code=400,
            )
        notifications = await self.repo.list_notifications(user_id=user_id, limit=limit)
        return [self._to_notification_view(notification) for notification in notifications]

    async def update_conversation_settings(
        self,
        *,
        conversation_id: str,
        user_id: str,
        notification_level: str | None,
        muted_until: datetime | None,
        set_muted_until: bool,
    ):
        participant = await self.repo.update_participant_settings(
            conversation_id=conversation_id,
            user_id=user_id,
            notification_level=notification_level,
            muted_until=muted_until,
            set_muted_until=set_muted_until,
        )
        if participant is None:
            raise AppError(
                code="CONVERSATION_NOT_FOUND",
                message="Conversation not found",
                status_code=404,
            )
        return participant

    async def update_preferences(
        self, *, user_id: str, body: NotificationPreferencesRequest
    ):
        updates: dict[str, Any] = {}
        if "timezone" in body.model_fields_set:
            updates["timezone"] = self._strip_or_none(body.timezone)
        if "dnd_from" in body.model_fields_set:
            updates["dnd_from"] = self._strip_or_none(body.dnd_from)
        if "dnd_to" in body.model_fields_set:
            updates["dnd_to"] = self._strip_or_none(body.dnd_to)
        if "notification_keywords" in body.model_fields_set:
            updates["notification_keywords"] = self._normalize_keywords(
                body.notification_keywords or []
            )
        user = await self.repo.update_user_preferences(user_id=user_id, updates=updates)
        if user is None:
            raise AppError(
                code="USER_NOT_FOUND",
                message="User not found",
                status_code=404,
            )
        return user

    async def register_push_token(
        self, *, user_id: str, body: PushTokenRegisterRequest
    ) -> PushTokenView:
        token = await self.repo.upsert_push_token(
            user_id=user_id,
            device_id=self._strip_or_none(body.device_id),
            platform=body.platform,
            token=body.token.strip(),
        )
        return self._to_push_token_view(token)

    async def remove_push_token(
        self, *, user_id: str, device_id: str | None, token: str | None
    ) -> int:
        return await self.repo.remove_push_token(
            user_id=user_id,
            device_id=self._strip_or_none(device_id),
            token=self._strip_or_none(token),
        )

    async def prune_invalid_tokens(self, *, tokens: list[str]) -> int:
        return await self.repo.prune_push_tokens(tokens=tokens)

    def _is_muted(self, muted_until: datetime | None, now: datetime) -> bool:
        if muted_until is None:
            return False
        normalized = (
            muted_until if muted_until.tzinfo is not None else muted_until.replace(tzinfo=UTC)
        )
        return normalized > now

    def _is_mentioned(self, *, message: MessageDoc, user_id: str) -> bool:
        return (
            user_id in message.mention_user_ids
            or message.mention_scope in {"all", "here"}
        )

    def _matched_keyword(self, *, text: str | None, keywords: list[str]) -> str | None:
        normalized_text = (text or "").lower()
        for keyword in keywords:
            normalized_keyword = keyword.strip().lower()
            if normalized_keyword and normalized_keyword in normalized_text:
                return keyword
        return None

    def _is_in_dnd_window(self, user: Any | None, now: datetime) -> bool:
        if user is None:
            return False
        dnd_from = getattr(user, "dnd_from", None)
        dnd_to = getattr(user, "dnd_to", None)
        if not dnd_from or not dnd_to:
            return False

        zone_name = getattr(user, "timezone", None) or "UTC"
        try:
            local_now = now.astimezone(ZoneInfo(zone_name))
        except ZoneInfoNotFoundError:
            local_now = now.astimezone(UTC)

        start = self._parse_clock_time(dnd_from)
        end = self._parse_clock_time(dnd_to)
        if start is None or end is None:
            return False

        current = local_now.time()
        if start <= end:
            return start <= current < end
        return current >= start or current < end

    def _parse_clock_time(self, value: str) -> time | None:
        try:
            hour, minute = value.split(":", 1)
            return time(hour=int(hour), minute=int(minute))
        except (TypeError, ValueError):
            return None

    async def _enqueue_push(
        self, *, notification: NotificationView, tokens: list[str]
    ) -> None:
        if self.queue is None:
            return
        await self.queue.publish(
            queue_name=self.push_queue_name,
            payload={
                "type": "deliver_push_notification",
                "notification_id": notification.id,
                "user_id": notification.user_id,
                "tokens": tokens,
                "data": notification.data,
            },
        )

    def _normalize_keywords(self, keywords: list[str]) -> list[str]:
        return list(
            dict.fromkeys(
                keyword.strip()
                for keyword in keywords
                if keyword.strip()
            )
        )

    def _strip_or_none(self, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None

    def _to_notification_view(self, notification) -> NotificationView:
        return NotificationView(
            id=notification.str_id,
            user_id=str(notification.user_id),
            kind=notification.kind,
            actor_user_id=str(notification.actor_user_id),
            resource_type=notification.resource_type,
            resource_id=str(notification.resource_id),
            message_id=(
                str(notification.message_id)
                if notification.message_id is not None
                else None
            ),
            read_at=notification.read_at,
            data=notification.data,
            created_at=notification.created_at,
            updated_at=notification.updated_at,
        )

    def _to_push_token_view(self, token) -> PushTokenView:
        return PushTokenView(
            id=token.str_id,
            user_id=str(token.user_id),
            device_id=token.device_id,
            platform=token.platform,
            token=token.token,
            created_at=token.created_at,
            updated_at=token.updated_at,
        )
