from __future__ import annotations

from datetime import UTC, datetime
from functools import partial
from typing import Any

from beanie.operators import In
from pymongo.errors import DuplicateKeyError

from app.core.errors import AppError
from app.db.models import UserDocument
from app.db.object_id import parse_object_id
from app.db.repository import BaseRepository
from app.modules.auth.username import generate_username_candidate, normalize_username

_oid = partial(parse_object_id, message="Invalid user id")


class UsersRepository(BaseRepository[UserDocument]):
    model = UserDocument

    async def _get_or_404(self, user_id: str) -> UserDocument:
        return await self.get_or_404(
            user_id,
            code="USER_NOT_FOUND",
            message="User not found",
            invalid_message="Invalid user id",
        )

    async def _generate_unique_username(self, max_attempts: int = 25) -> str:
        for _ in range(max_attempts):
            candidate = normalize_username(generate_username_candidate())
            if await UserDocument.find_one(UserDocument.username == candidate) is None:
                return candidate

        raise AppError(
            code="USERNAME_GENERATION_FAILED",
            message="Could not generate username",
            status_code=500,
        )

    def _build_new_user_doc(self, email: str, username: str) -> UserDocument:
        now = datetime.now(UTC)
        return UserDocument(
            email=email.lower().strip(),
            username=normalize_username(username),
            created_at=now,
            updated_at=now,
        )

    async def create_user(self, email: str) -> UserDocument:
        username = await self._generate_unique_username()
        doc = self._build_new_user_doc(email, username)
        try:
            await doc.insert()
            return doc
        except DuplicateKeyError as exc:
            raise AppError(
                code="EMAIL_ALREADY_EXISTS",
                message="Email already registered",
                status_code=409,
            ) from exc

    async def create_if_not_exists(self, email: str) -> UserDocument:
        """Idempotent register: return existing user, else create one."""
        email_n = email.lower().strip()
        existing = await UserDocument.find_one(UserDocument.email == email_n)
        if existing:
            return existing

        username = await self._generate_unique_username()
        doc = self._build_new_user_doc(email_n, username)
        try:
            await doc.insert()
            return doc
        except DuplicateKeyError:
            existing2 = await UserDocument.find_one(UserDocument.email == email_n)
            if existing2:
                return existing2
            raise

    async def find_by_id(self, user_id: str) -> UserDocument | None:
        return await self.get_by_id(user_id, invalid_message="Invalid user id")

    async def find_by_ids(self, user_ids: list[str]) -> dict[str, UserDocument]:
        if not user_ids:
            return {}

        unique_ids = list(dict.fromkeys(user_ids))
        object_ids = [_oid(user_id) for user_id in unique_ids]
        docs = await UserDocument.find(In(UserDocument.id, object_ids)).to_list()
        return {str(doc.id): doc for doc in docs if doc.id is not None}

    async def find_by_email(self, email: str) -> UserDocument | None:
        return await UserDocument.find_one(UserDocument.email == email.lower().strip())

    async def find_by_username(self, username: str) -> UserDocument | None:
        return await UserDocument.find_one(
            UserDocument.username == normalize_username(username)
        )

    async def find_by_username_prefix(self, q: str, limit: int) -> list[UserDocument]:
        return (
            await UserDocument.find({"username": {"$regex": f"^{q}", "$options": "i"}})
            .limit(limit)
            .to_list()
        )

    async def set_verified(self, user_id: str) -> None:
        user = await self._get_or_404(user_id)
        await user.set(
            {
                UserDocument.is_verified: True,
                UserDocument.updated_at: datetime.now(UTC),
            }
        )

    async def update_profile(
        self,
        *,
        user_id: str,
        display_name: str | None,
        bio: str | None,
        is_private: bool | None,
        default_discovery_enabled: bool | None,
    ) -> UserDocument:
        updates: dict[Any, Any] = {UserDocument.updated_at: datetime.now(UTC)}
        if display_name is not None:
            updates[UserDocument.display_name] = display_name
        if bio is not None:
            updates[UserDocument.bio] = bio
        if is_private is not None:
            updates[UserDocument.is_private] = is_private
        if default_discovery_enabled is not None:
            updates[UserDocument.default_discovery_enabled] = default_discovery_enabled

        user = await self._get_or_404(user_id)
        return await user.set(updates)

    async def update_username(self, *, user_id: str, username: str) -> UserDocument:
        now = datetime.now(UTC)
        normalized = normalize_username(username)
        user = await self._get_or_404(user_id)
        try:
            return await user.set(
                {
                    UserDocument.username: normalized,
                    UserDocument.username_updated_at: now,
                    UserDocument.updated_at: now,
                }
            )
        except DuplicateKeyError as exc:
            raise AppError(
                code="USERNAME_TAKEN",
                message="Username already taken",
                status_code=409,
            ) from exc

    async def update_avatar(
        self,
        *,
        user_id: str,
        avatar: dict[str, Any] | None,
    ) -> UserDocument:
        now = datetime.now(UTC)
        user = await self._get_or_404(user_id)
        return await user.set(
            {UserDocument.avatar: avatar, UserDocument.updated_at: now}
        )

    async def set_has_passkey(self, *, user_id: str, value: bool) -> None:
        user = await self._get_or_404(user_id)
        await user.set(
            {
                UserDocument.has_passkey: value,
                UserDocument.updated_at: datetime.now(UTC),
            }
        )

    async def set_passkey_login_enabled(self, *, user_id: str, value: bool) -> None:
        user = await self._get_or_404(user_id)
        await user.set(
            {
                UserDocument.passkey_login_enabled: value,
                UserDocument.updated_at: datetime.now(UTC),
            }
        )
