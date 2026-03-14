from __future__ import annotations

from datetime import datetime, UTC
from typing import Any, Optional

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo.errors import DuplicateKeyError

from app.core.errors import AppError
from app.db.indexes import COL_USERS
from app.modules.auth.username import normalize_username, generate_username_candidate


def _oid(s: str) -> ObjectId:
    try:
        return ObjectId(s)
    except Exception:
        raise AppError(code="INVALID_ID", message="Invalid user id", status_code=400)


class UsersRepository:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.col = db[COL_USERS]

    async def _generate_unique_username(self, max_attempts: int = 25) -> str:
        for _ in range(max_attempts):
            candidate = normalize_username(generate_username_candidate())
            existing = await self.col.find_one({"username": candidate}, {"_id": 1})
            if not existing:
                return candidate

        raise AppError(
            code="USERNAME_GENERATION_FAILED",
            message="Could not generate username",
            status_code=500,
        )

    def _build_new_user_doc(self, email: str, username: str) -> dict[str, Any]:
        now = datetime.now(UTC)
        return {
            "email": email.lower().strip(),
            "is_verified": False,
            "username": normalize_username(username),
            "display_name": None,
            "bio": None,
            "avatar": None,
            "is_private": False,
            "default_discovery_enabled": True,
            "last_seen_at": None,
            "username_updated_at": None,
            "has_passkey": False,
            "passkey_login_enabled": True,
            "created_at": now,
            "updated_at": now,
        }

    async def create_user(self, email: str) -> dict[str, Any]:
        username = await self._generate_unique_username()
        doc = self._build_new_user_doc(email, username)

        try:
            res = await self.col.insert_one(doc)
        except DuplicateKeyError as exc:
            raise AppError(
                code="EMAIL_ALREADY_EXISTS",
                message="Email already registered",
                status_code=409,
            ) from exc
        doc["_id"] = res.inserted_id
        return doc

    async def create_if_not_exists(self, email: str) -> dict[str, Any]:
        """
        Idempotent-ish register: if user exists, return it; else create.
        Avoids leaking info and supports "register again" behavior.
        """
        email_n = email.lower().strip()
        existing = await self.col.find_one({"email": email_n})
        if existing:
            return existing

        username = await self._generate_unique_username()
        doc = self._build_new_user_doc(email_n, username)

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

    async def find_by_id(self, user_id: str) -> Optional[dict[str, Any]]:
        return await self.col.find_one({"_id": _oid(user_id)})

    async def find_by_email(self, email: str) -> Optional[dict[str, Any]]:
        return await self.col.find_one({"email": email.lower().strip()})

    async def find_by_username(self, username: str) -> Optional[dict[str, Any]]:
        return await self.col.find_one({"username": normalize_username(username)})

    async def find_by_username_prefix(self, q: str, limit: int) -> list[dict[str, Any]]:
        cursor = self.col.find(
            {"username": {"$regex": f"^{q}", "$options": "i"}},
            {
                "_id": 1,
                "username": 1,
                "display_name": 1,
                "avatar": 1,
                "is_private": 1,
                "default_discovery_enabled": 1,
            },
        ).limit(limit)
        return await cursor.to_list(length=limit)

    async def set_verified(self, user_id: str) -> None:
        res = await self.col.update_one(
            {"_id": _oid(user_id)},
            {"$set": {"is_verified": True, "updated_at": datetime.now(UTC)}},
        )
        if res.matched_count == 0:
            raise AppError(code="USER_NOT_FOUND", message="User not found", status_code=404)

    async def update_profile(
            self,
            *,
            user_id: str,
            display_name: str | None,
            bio: str | None,
            is_private: bool | None,
            default_discovery_enabled: bool | None,
    ) -> dict[str, Any]:
        updates: dict[str, Any] = {"updated_at": datetime.now(UTC)}

        if display_name is not None:
            updates["display_name"] = display_name
        if bio is not None:
            updates["bio"] = bio
        if is_private is not None:
            updates["is_private"] = is_private
        if default_discovery_enabled is not None:
            updates["default_discovery_enabled"] = default_discovery_enabled

        res = await self.col.update_one({"_id": _oid(user_id)}, {"$set": updates})
        if res.matched_count == 0:
            raise AppError(code="USER_NOT_FOUND", message="User not found", status_code=404)

        user = await self.find_by_id(user_id)
        if not user:
            raise AppError(code="USER_NOT_FOUND", message="User not found", status_code=404)

        return user

    async def update_username(self, *, user_id: str, username: str) -> dict[str, Any]:
        now = datetime.now(UTC)
        normalized = normalize_username(username)

        try:
            res = await self.col.update_one(
                {"_id": _oid(user_id)},
                {
                    "$set": {
                        "username": normalized,
                        "username_updated_at": now,
                        "updated_at": now,
                    }
                },
            )
        except DuplicateKeyError as exc:
            raise AppError(
                code="USERNAME_TAKEN",
                message="Username already taken",
                status_code=409,
            ) from exc

        if res.matched_count == 0:
            raise AppError(code="USER_NOT_FOUND", message="User not found", status_code=404)

        user = await self.find_by_id(user_id)
        if not user:
            raise AppError(code="USER_NOT_FOUND", message="User not found", status_code=404)

        return user

    async def update_avatar(
        self,
        *,
        user_id: str,
        avatar: dict[str, Any] | None,
    ) -> dict[str, Any]:
        now = datetime.now(UTC)

        if avatar is None:
            res = await self.col.update_one(
                {"_id": _oid(user_id)},
                {
                    "$unset": {"avatar": ""},
                    "$set": {"updated_at": now},
                },
            )
        else:
            res = await self.col.update_one(
                {"_id": _oid(user_id)},
                {
                    "$set": {
                        "avatar": avatar,
                        "updated_at": now,
                    }
                },
            )

        if res.matched_count == 0:
            raise AppError(code="USER_NOT_FOUND", message="User not found", status_code=404)

        user = await self.find_by_id(user_id)
        if not user:
            raise AppError(code="USER_NOT_FOUND", message="User not found", status_code=404)

        return user

    async def set_has_passkey(self, *, user_id: str, value: bool) -> None:
        res = await self.col.update_one(
            {"_id": _oid(user_id)},
            {
                "$set": {
                    "has_passkey": value,
                    "updated_at": datetime.now(UTC),
                }
            },
        )
        if res.matched_count == 0:
            raise AppError(code="USER_NOT_FOUND", message="User not found", status_code=404)

    async def set_passkey_login_enabled(self, *, user_id: str, value: bool) -> None:
        res = await self.col.update_one(
            {"_id": _oid(user_id)},
            {
                "$set": {
                    "passkey_login_enabled": value,
                    "updated_at": datetime.now(UTC),
                }
            },
        )
        if res.matched_count == 0:
            raise AppError(code="USER_NOT_FOUND", message="User not found", status_code=404)