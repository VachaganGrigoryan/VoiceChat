from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.modules.messages.schemas import (
    MessageContent,
    MessageDoc,
    MessagePlaintext,
    MessageReceiptSummary,
)
from app.modules.notifications.service import NotificationsService


def _participant(
    user_id: str,
    *,
    level: str = "all",
    muted_until: datetime | None = None,
):
    return SimpleNamespace(
        user_id=user_id,
        notification_level=level,
        muted_until=muted_until,
    )


def _user(
    *,
    keywords: list[str] | None = None,
    dnd_from: str | None = None,
    dnd_to: str | None = None,
):
    return SimpleNamespace(
        notification_keywords=keywords or [],
        dnd_from=dnd_from,
        dnd_to=dnd_to,
        timezone="UTC",
    )


def _message(
    *,
    sender_id: str = "sender",
    text: str = "hello",
    mention_user_ids: list[str] | None = None,
    mention_scope: str | None = None,
) -> MessageDoc:
    now = datetime.now(UTC)
    return MessageDoc(
        id="507f1f77bcf86cd799439011",
        container_type="conversation",
        container_id="conversation-1",
        sender_id=sender_id,
        type="text",
        content=MessageContent(
            encryption="none",
            type="text",
            plaintext=MessagePlaintext(text=text),
        ),
        receipt_summary=MessageReceiptSummary(),
        mention_user_ids=mention_user_ids or [],
        mention_scope=mention_scope,
        created_at=now,
        updated_at=now,
    )


class FakeNotificationsRepository:
    def __init__(self, *, participants, users, push_tokens=None):
        self.participants = participants
        self.users = users
        self.push_tokens = push_tokens or {}
        self.created = []

    async def list_conversation_participants(self, *, conversation_id: str):
        return self.participants

    async def users_by_ids(self, user_ids: list[str]):
        return {user_id: self.users[user_id] for user_id in user_ids}

    async def push_tokens_for_user(self, *, user_id: str):
        return self.push_tokens.get(user_id, [])

    async def create_notification(
        self,
        *,
        user_id: str,
        kind: str,
        source_type: str | None,
        source_id: str | None,
        conversation_id: str | None,
        data: dict,
    ):
        now = datetime.now(UTC)
        notification = SimpleNamespace(
            str_id=f"notification-{len(self.created) + 1}",
            user_id=user_id,
            kind=kind,
            source_type=source_type,
            source_id=source_id,
            conversation_id=conversation_id,
            read_at=None,
            data=data,
            created_at=now,
            updated_at=now,
        )
        self.created.append(notification)
        return notification


@pytest.mark.asyncio
async def test_message_notifications_honor_levels_mutes_dnd_keywords_and_sender():
    now = datetime.now(UTC)
    participants = [
        _participant("sender", level="all"),
        _participant("mentions-user", level="mentions"),
        _participant("mentions-suppressed", level="mentions"),
        _participant("keyword-user", level="none"),
        _participant("muted-user", level="all", muted_until=now + timedelta(hours=1)),
        _participant("dnd-user", level="all"),
    ]
    repo = FakeNotificationsRepository(
        participants=participants,
        users={
            "sender": _user(),
            "mentions-user": _user(),
            "mentions-suppressed": _user(),
            "keyword-user": _user(keywords=["urgent"]),
            "muted-user": _user(),
            "dnd-user": _user(dnd_from="00:00", dnd_to="23:59"),
        },
    )
    service = NotificationsService(repo=repo)

    generated = await service.generate_for_message(
        message=_message(
            text="urgent launch update",
            mention_user_ids=["mentions-user"],
        )
    )

    notified_user_ids = {item.notification.user_id for item in generated}
    assert notified_user_ids == {"mentions-user", "keyword-user", "dnd-user"}
    assert "sender" not in notified_user_ids
    assert "mentions-suppressed" not in notified_user_ids
    assert "muted-user" not in notified_user_ids
    assert {
        item.notification.user_id
        for item in generated
        if item.push_suppressed_by_dnd
    } == {"dnd-user"}


@pytest.mark.asyncio
async def test_expired_mute_resumes_notifications():
    repo = FakeNotificationsRepository(
        participants=[
            _participant("sender", level="all"),
            _participant(
                "expired-muted-user",
                level="all",
                muted_until=datetime.now(UTC) - timedelta(minutes=1),
            ),
        ],
        users={
            "sender": _user(),
            "expired-muted-user": _user(),
        },
    )
    service = NotificationsService(repo=repo)

    generated = await service.generate_for_message(message=_message())

    assert [item.notification.user_id for item in generated] == [
        "expired-muted-user"
    ]
