from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field
from pymongo import ASCENDING, IndexModel

from app.db.document import BaseDocument
from app.db.collections import COL_PASSKEY_CHALLENGES, COL_PASSKEYS
from app.db.object_id import StrId


class PasskeyDocument(BaseDocument):
    user_id: StrId
    credential_id: str
    public_key: str
    sign_count: int = Field(ge=0)
    transports: list[str] | None = None
    device_type: str | None = None
    backed_up: bool | None = None
    nickname: str | None = None
    aaguid: str | None = None
    last_used_at: datetime | None = None
    created_at: datetime
    updated_at: datetime

    class Settings:
        name = COL_PASSKEYS
        indexes = [
            IndexModel(
                [("credential_id", ASCENDING)],
                unique=True,
                name="uniq_passkeys_credential_id",
            ),
            IndexModel([("user_id", ASCENDING)], name="idx_passkeys_user_id"),
        ]


class PasskeyChallengeDocument(BaseDocument):
    flow: Literal["register", "authenticate"]
    challenge: str
    expires_at: datetime
    used_at: datetime | None = None
    created_at: datetime
    user_id: StrId | None = None
    email: str | None = None

    class Settings:
        name = COL_PASSKEY_CHALLENGES
        indexes = [
            IndexModel(
                [("challenge", ASCENDING)],
                name="idx_passkey_challenges_challenge",
            ),
            IndexModel(
                [("expires_at", ASCENDING)],
                expireAfterSeconds=0,
                name="idx_passkey_challenges_expires_at",
            ),
            IndexModel(
                [("flow", ASCENDING), ("user_id", ASCENDING), ("email", ASCENDING)],
                name="idx_passkey_challenges_flow_user_email",
            ),
        ]
