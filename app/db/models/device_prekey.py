from __future__ import annotations

from pymongo import ASCENDING, IndexModel

from app.db.collections import COL_DEVICE_PREKEYS
from app.db.document import TimestampedDocument
from app.db.object_id import StrId


class DevicePreKeyDocument(TimestampedDocument):
    """A single prekey uploaded by a device for future E2EE key distribution.

    Scaffolding only: ``public_key``/``signature`` are opaque strings and are not
    cryptographically verified. One-time prekeys are marked ``consumed`` when served
    in a bundle.
    """

    user_id: StrId
    device_id: str
    key_id: int
    public_key: str
    signature: str | None = None
    one_time: bool = True
    consumed: bool = False

    class Settings:
        name = COL_DEVICE_PREKEYS
        indexes = [
            IndexModel(
                [("device_id", ASCENDING), ("key_id", ASCENDING)],
                unique=True,
                name="ux_device_prekeys_device_key",
            ),
            IndexModel(
                [
                    ("user_id", ASCENDING),
                    ("device_id", ASCENDING),
                    ("one_time", ASCENDING),
                    ("consumed", ASCENDING),
                ],
                name="ix_device_prekeys_bundle_lookup",
            ),
        ]
