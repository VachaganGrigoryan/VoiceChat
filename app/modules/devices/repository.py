from __future__ import annotations

from datetime import UTC, datetime

from pymongo import ReturnDocument

from app.db.models import DeviceDocument, DevicePreKeyDocument
from app.db.repository import BaseRepository


class DevicesRepository(BaseRepository[DeviceDocument]):
    model = DeviceDocument

    async def upsert_device(
        self,
        *,
        user_id: str,
        device_id: str,
        name: str | None,
        platform: str | None,
        identity_public_key: str | None,
        signing_public_key: str | None,
        registration_id: int | None,
    ) -> DeviceDocument:
        now = datetime.now(UTC)
        raw = await self.raw.find_one_and_update(
            {"user_id": str(user_id), "device_id": device_id},
            {
                "$set": {
                    "user_id": str(user_id),
                    "device_id": device_id,
                    "name": name,
                    "platform": platform,
                    "identity_public_key": identity_public_key,
                    "signing_public_key": signing_public_key,
                    "registration_id": registration_id,
                    "last_seen_at": now,
                    "updated_at": now,
                },
                "$setOnInsert": {"created_at": now},
            },
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
        return DeviceDocument.model_validate(raw)

    async def get_device(
        self, *, user_id: str, device_id: str
    ) -> DeviceDocument | None:
        return await DeviceDocument.find_one(
            {"user_id": str(user_id), "device_id": device_id}
        )

    async def first_device_for_user(self, *, user_id: str) -> DeviceDocument | None:
        return (
            await DeviceDocument.find({"user_id": str(user_id)})
            .sort("created_at")
            .first_or_none()
        )

    async def add_prekeys(
        self,
        *,
        user_id: str,
        device_id: str,
        prekeys: list[dict],
    ) -> int:
        if not prekeys:
            return 0
        now = datetime.now(UTC)
        docs = [
            DevicePreKeyDocument(
                user_id=str(user_id),
                device_id=device_id,
                key_id=int(pk["key_id"]),
                public_key=pk["public_key"],
                signature=pk.get("signature"),
                one_time=bool(pk.get("one_time", True)),
                consumed=False,
                created_at=now,
                updated_at=now,
            )
            for pk in prekeys
        ]
        await DevicePreKeyDocument.insert_many(docs)
        return len(docs)

    async def take_one_time_prekey(
        self, *, user_id: str, device_id: str
    ) -> DevicePreKeyDocument | None:
        """Atomically claim an unconsumed one-time prekey, marking it consumed."""
        raw = await DevicePreKeyDocument.get_pymongo_collection().find_one_and_update(
            {
                "user_id": str(user_id),
                "device_id": device_id,
                "one_time": True,
                "consumed": False,
            },
            {"$set": {"consumed": True, "updated_at": datetime.now(UTC)}},
            return_document=ReturnDocument.AFTER,
        )
        return DevicePreKeyDocument.model_validate(raw) if raw is not None else None
