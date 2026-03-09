from __future__ import annotations

from typing import Any

from fastapi import UploadFile

from app.core.errors import AppError
from app.modules.auth.repository import UsersRepository
from app.modules.auth.username import is_valid_username, normalize_username
from app.modules.users.schemas import UpdateProfileRequest, UserProfileResponse
from app.infra.storage import get_storage, MediaStorageService

ALLOWED_AVATAR_CONTENT_TYPES: dict[str, str] = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}

MAX_AVATAR_SIZE_BYTES = 5 * 1024 * 1024


def _strip_or_none(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value or None


class UsersService:
    def __init__(self, users: UsersRepository):
        self.users = users

    async def get_me(self, *, user_id: str) -> UserProfileResponse:
        user = await self.users.find_by_id(user_id)
        if not user:
            raise AppError(code="USER_NOT_FOUND", message="User not found", status_code=404)

        return self._to_profile_response(user)

    async def update_me(
        self,
        *,
        user_id: str,
        body: UpdateProfileRequest,
    ) -> UserProfileResponse:
        user = await self.users.update_profile(
            user_id=user_id,
            display_name=_strip_or_none(body.display_name),
            bio=_strip_or_none(body.bio),
            is_private=body.is_private,
            default_discovery_enabled=body.default_discovery_enabled,
        )
        return self._to_profile_response(user)

    async def update_username(
        self,
        *,
        user_id: str,
        username: str,
    ) -> UserProfileResponse:
        username_n = normalize_username(username)

        if not is_valid_username(username_n):
            raise AppError(
                code="INVALID_USERNAME",
                message="Username format is invalid",
                status_code=400,
            )

        existing = await self.users.find_by_username(username_n)
        if existing and str(existing["_id"]) != user_id:
            raise AppError(
                code="USERNAME_TAKEN",
                message="Username already taken",
                status_code=409,
            )

        user = await self.users.update_username(user_id=user_id, username=username_n)
        return self._to_profile_response(user)

    async def upload_avatar(
        self,
        *,
        user_id: str,
        file: UploadFile,
    ) -> UserProfileResponse:
        user = await self.users.find_by_id(user_id)
        if not user:
            raise AppError(code="USER_NOT_FOUND", message="User not found", status_code=404)

        content_type = (file.content_type or "").lower().strip()
        content = await self._read_avatar_bytes(file)

        storage = MediaStorageService()
        stored = await storage.save_avatar(
            user_id=user_id,
            filename=file.filename,
            content=content,
            mime=content_type,
        )

        avatar = {
            "storage": stored.storage,
            "key": stored.key,
            "url": stored.url,
            "mime": stored.mime,
            "size_bytes": stored.size_bytes,
        }

        previous_avatar = user.get("avatar")
        updated = await self.users.update_avatar(user_id=user_id, avatar=avatar)

        # best-effort cleanup of previous avatar
        if previous_avatar and isinstance(previous_avatar, dict):
            prev_key = previous_avatar.get("key")
            if prev_key and prev_key != avatar.key:
                try:
                    await storage.delete(prev_key)
                except Exception:
                    pass

        return self._to_profile_response(updated)

    async def delete_avatar(self, *, user_id: str) -> UserProfileResponse:
        user = await self.users.find_by_id(user_id)
        if not user:
            raise AppError(code="USER_NOT_FOUND", message="User not found", status_code=404)

        avatar = user.get("avatar")

        if avatar and isinstance(avatar, dict):
            key = avatar.get("key")
            if key:
                storage = get_storage()
                try:
                    await storage.delete(key)
                except Exception:
                    pass

        updated = await self.users.update_avatar(user_id=user_id, avatar=None)
        return self._to_profile_response(updated)

    def _to_profile_response(self, user: dict[str, Any]) -> UserProfileResponse:
        return UserProfileResponse(
            id=str(user["_id"]),
            email=user["email"],
            is_verified=bool(user.get("is_verified", False)),
            username=user.get("username", ""),
            display_name=user.get("display_name"),
            bio=user.get("bio"),
            avatar=user.get("avatar"),
            is_private=bool(user.get("is_private", False)),
            default_discovery_enabled=bool(user.get("default_discovery_enabled", True)),
            last_seen_at=user.get("last_seen_at"),
            username_updated_at=user.get("username_updated_at"),
            created_at=user["created_at"],
            updated_at=user["updated_at"],
        )

    async def _read_avatar_bytes(self, file: UploadFile) -> bytes:
        content_type = (file.content_type or "").lower().strip()
        if content_type not in ALLOWED_AVATAR_CONTENT_TYPES:
            raise AppError(
                code="UNSUPPORTED_AVATAR_TYPE",
                message="Avatar must be jpeg, png, or webp",
                status_code=400,
            )

        data = await file.read()
        if not data:
            raise AppError(
                code="EMPTY_FILE",
                message="Uploaded file is empty",
                status_code=400,
            )

        if len(data) > MAX_AVATAR_SIZE_BYTES:
            raise AppError(
                code="AVATAR_TOO_LARGE",
                message="Avatar must be at most 5 MB",
                status_code=400,
            )

        return data