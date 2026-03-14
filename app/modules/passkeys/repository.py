from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ReturnDocument


class PasskeysRepository:
    def __init__(self, db: AsyncIOMotorDatabase) -> None:
        self.collection = db["passkeys"]

    async def create_passkey(self, doc: dict[str, Any]) -> dict[str, Any]:
        result = await self.collection.insert_one(doc)
        doc["_id"] = result.inserted_id
        return doc

    async def find_by_credential_id(self, credential_id: str) -> dict[str, Any] | None:
        return await self.collection.find_one({"credential_id": credential_id})

    async def list_by_user_id(self, user_id: str) -> list[dict[str, Any]]:
        cursor = self.collection.find({"user_id": user_id}).sort("created_at", -1)
        return [doc async for doc in cursor]

    async def delete_by_credential_id(self, user_id: str, credential_id: str) -> bool:
        result = await self.collection.delete_one({"user_id": user_id, "credential_id": credential_id})
        return result.deleted_count > 0

    async def update_sign_count(self, credential_id: str, sign_count: int, now: datetime) -> None:
        await self.collection.update_one(
            {"credential_id": credential_id},
            {"$set": {"sign_count": sign_count, "last_used_at": now, "updated_at": now}},
        )

    async def count_by_user_id(self, user_id: str) -> int:
        return await self.collection.count_documents({"user_id": user_id})


class PasskeyChallengesRepository:
    def __init__(self, db: AsyncIOMotorDatabase) -> None:
        self.collection = db["passkey_challenges"]

    async def create_challenge(
        self,
        *,
        flow: Literal["register", "authenticate"],
        challenge: str,
        expires_at: datetime,
        user_id: str | None = None,
        email: str | None = None,
        now: datetime,
    ) -> dict[str, Any]:
        doc: dict[str, Any] = {
            "flow": flow,
            "challenge": challenge,
            "expires_at": expires_at,
            "used_at": None,
            "created_at": now,
            "user_id": user_id,
            "email": email,
        }
        result = await self.collection.insert_one(doc)
        doc["_id"] = result.inserted_id
        return doc

    async def consume_active_challenge(
        self,
        *,
        flow: Literal["register", "authenticate"],
        challenge: str,
        now: datetime,
        user_id: str | None = None,
        email: str | None = None,
    ) -> dict[str, Any] | None:
        query: dict[str, Any] = {
            "flow": flow,
            "challenge": challenge,
            "used_at": None,
            "expires_at": {"$gt": now},
        }
        if user_id is not None:
            query["user_id"] = user_id
        if email is not None:
            query["email"] = email
        return await self.collection.find_one_and_update(
            query,
            {"$set": {"used_at": now}},
            return_document=ReturnDocument.AFTER,
        )
