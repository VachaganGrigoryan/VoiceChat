from __future__ import annotations

from datetime import datetime
from typing import Any

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
