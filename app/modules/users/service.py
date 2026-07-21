from __future__ import annotations

from typing import Any, Protocol

from bson import ObjectId
from fastapi import UploadFile

from app.core.errors import AppError
from app.db.models import UserDocument
from app.modules.auth.repository import UsersRepository
from app.modules.auth.username import is_valid_username, normalize_username
from app.modules.pings.repository import PingsRepository
from app.modules.users.avatar import build_user_avatar_payload
from app.modules.users.schemas import (
    SelectedUserProfileResponse,
    UpdateProfileRequest,
    UserProfileResponse,
)
from app.infra.storage import get_storage, storage_key_builder

ALLOWED_AVATAR_CONTENT_TYPES: dict[str, str] = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}

MAX_AVATAR_SIZE_BYTES = 5 * 1024 * 1024


class PresenceServiceProto(Protocol):
    async def is_online(self, user_id: str) -> bool: ...


def _strip_or_none(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value or None


def _doc_value(doc: object, key: str, default: Any = None) -> Any:
    if isinstance(doc, dict):
        value = doc.get("_id", default) if key == "id" else doc.get(key, default)
    else:
        value = getattr(doc, key, default)
    return str(value) if isinstance(value, ObjectId) else value


class UsersService:
    def __init__(
        self,
        users: UsersRepository,
        pings: PingsRepository,
        presence_service: "PresenceServiceProto | None" = None,
    ):
        self.users = users
        self.pings = pings
        self.presence_service = presence_service

    async def get_me(self, *, user_id: str) -> UserProfileResponse:
        user = await self.users.find_by_id(user_id)
        if not user:
            raise AppError(
                code="USER_NOT_FOUND", message="User not found", status_code=404
            )

        return self._to_profile_response(user)

    async def get_user_profile(
        self,
        *,
        current_user_id: str,
        selected_user_id: str,
    ) -> SelectedUserProfileResponse:
        user = await self.users.find_by_id(selected_user_id)
        if not user:
            raise AppError(
                code="USER_NOT_FOUND", message="User not found", status_code=404
            )

        if current_user_id != selected_user_id:
            has_access = await self.pings.has_accepted_permission(
                user_a=current_user_id,
                user_b=selected_user_id,
            )
            if not has_access:
                raise AppError(
                    code="PROFILE_ACCESS_FORBIDDEN",
                    message="Accepted ping required to access this profile",
                    status_code=403,
                )

        return await self._to_selected_profile_response(user)

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
        if existing and _doc_value(existing, "id") != user_id:
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
            raise AppError(
                code="USER_NOT_FOUND", message="User not found", status_code=404
            )

        content_type = (file.content_type or "").lower().strip()
        content = await self._read_avatar_bytes(file)

        storage = get_storage()
        key_builder = storage_key_builder("avatar")

        stored = await storage.save(
            filename=file.filename,
            content=content,
            mime=content_type,
            key=key_builder(user_id, file.filename),
        )

        avatar = {
            "storage": stored.storage,
            "key": stored.key,
            "url": stored.url,
            "mime": stored.mime,
            "size_bytes": stored.size_bytes,
        }

        previous_avatar = _doc_value(user, "avatar")
        updated = await self.users.update_avatar(user_id=user_id, avatar=avatar)

        # best-effort cleanup of previous avatar
        if previous_avatar and isinstance(previous_avatar, dict):
            prev_key = previous_avatar.get("key")
            if prev_key and prev_key != avatar["key"]:
                try:
                    await storage.delete(prev_key)
                except Exception:
                    pass

        return self._to_profile_response(updated)

    async def delete_avatar(self, *, user_id: str) -> UserProfileResponse:
        user = await self.users.find_by_id(user_id)
        if not user:
            raise AppError(
                code="USER_NOT_FOUND", message="User not found", status_code=404
            )

        avatar = _doc_value(user, "avatar")

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

    def _to_profile_response(self, user: UserDocument) -> UserProfileResponse:
        return UserProfileResponse(
            id=_doc_value(user, "id", ""),
            email=_doc_value(user, "email"),
            is_verified=_doc_value(user, "is_verified"),
            username=_doc_value(user, "username"),
            display_name=_doc_value(user, "display_name"),
            bio=_doc_value(user, "bio"),
            avatar=build_user_avatar_payload(_doc_value(user, "avatar")),
            is_private=_doc_value(user, "is_private"),
            default_discovery_enabled=_doc_value(user, "default_discovery_enabled"),
            last_seen_at=_doc_value(user, "last_seen_at"),
            username_updated_at=_doc_value(user, "username_updated_at"),
            status_emoji=_doc_value(user, "status_emoji"),
            status_text=_doc_value(user, "status_text"),
            status_expires_at=_doc_value(user, "status_expires_at"),
            pronouns=_doc_value(user, "pronouns"),
            timezone=_doc_value(user, "timezone"),
            dnd_from=_doc_value(user, "dnd_from"),
            dnd_to=_doc_value(user, "dnd_to"),
            notification_keywords=_doc_value(user, "notification_keywords", []),
            created_at=_doc_value(user, "created_at"),
            updated_at=_doc_value(user, "updated_at"),
        )

    async def _to_selected_profile_response(
        self, user: UserDocument
    ) -> SelectedUserProfileResponse:
        user_id = _doc_value(user, "id", "")
        is_online = (
            await self.presence_service.is_online(user_id)
            if self.presence_service
            else False
        )
        return SelectedUserProfileResponse(
            id=user_id,
            username=_doc_value(user, "username"),
            display_name=_doc_value(user, "display_name"),
            bio=_doc_value(user, "bio"),
            avatar=build_user_avatar_payload(_doc_value(user, "avatar")),
            status_emoji=_doc_value(user, "status_emoji"),
            status_text=_doc_value(user, "status_text"),
            status_expires_at=_doc_value(user, "status_expires_at"),
            pronouns=_doc_value(user, "pronouns"),
            timezone=_doc_value(user, "timezone"),
            is_online=is_online,
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
