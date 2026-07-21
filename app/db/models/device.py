from __future__ import annotations

from datetime import datetime

from pymongo import ASCENDING, IndexModel

from app.db.collections import COL_DEVICES
from app.db.document import TimestampedDocument
from app.db.object_id import StrId


class DeviceDocument(TimestampedDocument):
    """A user's registered device holding opaque public key material.

    E2EE scaffolding only: keys are stored verbatim as strings; no cryptographic
    validation or key agreement is performed in this change.
    """

    user_id: StrId
    device_id: str
    name: str | None = None
    platform: str | None = None
    identity_public_key: str | None = None
    signing_public_key: str | None = None
    registration_id: int | None = None
    last_seen_at: datetime | None = None

    class Settings:
        name = COL_DEVICES
        indexes = [
            IndexModel(
                [("user_id", ASCENDING), ("device_id", ASCENDING)],
                unique=True,
                name="ux_devices_user_device",
            ),
        ]
