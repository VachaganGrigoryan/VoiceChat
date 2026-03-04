from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo.errors import DuplicateKeyError

from app.core.exceptions import AppError
from app.db.indexes import COL_USERS


def _oid(s: str) -> ObjectId:
    try:
        return ObjectId(s)
    except Exception:
        raise AppError(code="INVALID_ID", message="Invalid user id", status_code=400)


class UsersRepository:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.col = db[COL_USERS]

    async def create_user(self, email: str) -> dict[str, Any]:
        doc = {
            "email": email.lower().strip(),
            "is_verified": False,
            "created_at": datetime.utcnow(),
            "updated_at": datetime.utcnow(),
        }
        try:
            res = await self.col.insert_one(doc)
        except DuplicateKeyError:
            raise AppError(code="EMAIL_ALREADY_EXISTS", message="Email already registered", status_code=409)
        doc["_id"] = res.inserted_id
        return doc

    async def find_by_email(self, email: str) -> Optional[dict[str, Any]]:
        return await self.col.find_one({"email": email.lower().strip()})

    async def find_by_id(self, user_id: str) -> Optional[dict[str, Any]]:
        return await self.col.find_one({"_id": _oid(user_id)})

    async def set_verified(self, user_id: str) -> None:
        res = await self.col.update_one(
            {"_id": _oid(user_id)},
            {"$set": {"is_verified": True, "updated_at": datetime.utcnow()}},
        )
        if res.matched_count == 0:
            raise AppError(code="USER_NOT_FOUND", message="User not found", status_code=404)

    async def create_if_not_exists(self, email: str) -> dict[str, Any]:
        """
        Idempotent-ish register: if user exists, return it; else create.
        Avoids leaking info and supports "register again" behavior.
        """
        email_n = email.lower().strip()
        existing = await self.col.find_one({"email": email_n})
        if existing:
            return existing

        doc = {
            "email": email_n,
            "is_verified": False,
            "created_at": datetime.utcnow(),
            "updated_at": datetime.utcnow(),
        }
        try:
            res = await self.col.insert_one(doc)
        except DuplicateKeyError:
            # race: someone created between find and insert
            existing2 = await self.col.find_one({"email": email_n})
            if existing2:
                return existing2
            raise
        doc["_id"] = res.inserted_id
        return doc