from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import Field
from pymongo import ASCENDING, IndexModel

from app.db.document import BaseDocument
from app.db.collections import COL_USERS


class UserDocument(BaseDocument):
    email: str
    is_verified: bool = False
    username: str
    display_name: str | None = None
    bio: str | None = None
    avatar: dict[str, Any] | None = None
    is_private: bool = False
    default_discovery_enabled: bool = True
    last_seen_at: datetime | None = None
    username_updated_at: datetime | None = None
    has_passkey: bool = False
    passkey_login_enabled: bool = True
    is_bot: bool = False
    # Public channel the user pins as their profile's main timeline (a
    # ConversationDocument id, type="channel", visibility="public"). Null until
    # the user creates/pins one.
    main_channel_id: str | None = None

    # Rich profile + notification preferences (finalize-messenger-conversation-model).
    status_emoji: str | None = None
    status_text: str | None = None
    status_expires_at: datetime | None = None
    pronouns: str | None = None
    timezone: str | None = None
    # Do-not-disturb window, "HH:MM" in the user's timezone.
    dnd_from: str | None = None
    dnd_to: str | None = None
    notification_keywords: list[str] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime

    class Settings:
        name = COL_USERS
        indexes = [
            IndexModel([("email", ASCENDING)], unique=True, name="ux_users_email"),
            IndexModel(
                [("username", ASCENDING)], unique=True, name="ux_users_username"
            ),
            IndexModel([("is_private", ASCENDING)], name="ix_users_is_private"),
        ]
