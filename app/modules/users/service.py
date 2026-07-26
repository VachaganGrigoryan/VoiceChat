from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Protocol

from bson import ObjectId
from fastapi import UploadFile

from app.core.errors import AppError
from app.db.models import ChannelDocument, UserDocument
from app.modules.auth.repository import UsersRepository
from app.modules.auth.username import is_valid_username, normalize_username
from app.modules.channels.repository import ChannelsRepository
from app.modules.channels.service import ChannelService
from app.modules.users.avatar import build_user_avatar_payload
from app.modules.users.schemas import (
    SelectedUserProfileResponse,
    UpdateProfileRequest,
    UpdateStatusRequest,
    UserChannelView,
    UserProfileResponse,
)
from app.modules.pings.schemas import ContactExtras, ContactState
from app.modules.realtime.presence import PresenceState
from app.infra.storage import get_storage, storage_key_builder

ALLOWED_AVATAR_CONTENT_TYPES: dict[str, str] = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}

MAX_AVATAR_SIZE_BYTES = 5 * 1024 * 1024

# Bound the profile channel list; a user is unlikely to own more than a handful.
MAX_PROFILE_CHANNELS = 50


class PresenceServiceProto(Protocol):
    async def is_online(self, user_id: str) -> bool: ...

    async def get_state(self, user_id: str) -> PresenceState: ...


class PingsServiceProto(Protocol):
    async def get_contact_state(
        self, *, viewer_user_id: str, peer_user_id: str
    ) -> ContactState: ...

    async def get_contact_extras(
        self, *, viewer_user_id: str, peer_user_id: str
    ) -> ContactExtras: ...

    async def shares_context(
        self, viewer_user_id: str, peer_user_id: str
    ) -> bool: ...


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
        pings: PingsServiceProto,
        presence_service: "PresenceServiceProto | None" = None,
        channels: "ChannelsRepository | None" = None,
    ):
        self.users = users
        self.pings = pings
        self.presence_service = presence_service
        self.channels = channels

    async def get_me(self, *, user_id: str) -> UserProfileResponse:
        user = await self.users.find_by_id(user_id)
        if not user:
            raise AppError(
                code="USER_NOT_FOUND", message="User not found", status_code=404
            )

        user = await self._clear_expired_status_if_needed(user)
        return self._to_profile_response(user)

    async def get_user_profile(
        self,
        *,
        current_user_id: str,
        selected_user_id: str,
        include: set[str] | None = None,
    ) -> SelectedUserProfileResponse:
        user = await self.users.find_by_id(selected_user_id)
        if not user:
            raise AppError(
                code="USER_NOT_FOUND", message="User not found", status_code=404
            )

        if current_user_id != selected_user_id:
            relationship = await self.pings.get_contact_state(
                viewer_user_id=current_user_id,
                peer_user_id=selected_user_id,
            )
            shares_ctx = await self.pings.shares_context(
                viewer_user_id=current_user_id,
                peer_user_id=selected_user_id,
            )
            if not isinstance(shares_ctx, bool):
                shares_ctx = False
        else:
            relationship = ContactState(
                can_ping=False,
                chat_allowed=False,
                ping_status="none",
            )
            shares_ctx = True

        extras: ContactExtras | None = None
        if include and "contact_details" in include and relationship.ping_status == "accepted":
            extras = await self.pings.get_contact_extras(
                viewer_user_id=current_user_id,
                peer_user_id=selected_user_id,
            )

        user = await self._clear_expired_status_if_needed(user)
        has_private_access = current_user_id == selected_user_id or (
            relationship.chat_allowed and not relationship.blocks_me
        )
        return await self._to_selected_profile_response(
            user,
            relationship=relationship,
            include_private_profile=has_private_access or not _doc_value(user, "is_private"),
            extras=extras,
            shares_context=shares_ctx,
        )

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
            pronouns=_strip_or_none(body.pronouns),
            timezone=_strip_or_none(body.timezone),
            is_private=body.is_private,
            default_discovery_enabled=body.default_discovery_enabled,
        )
        if body.is_private is not None and user.main_channel_id is not None:
            await ChannelService(repo=self._require_channels()).set_profile_privacy(
                channel_id=user.main_channel_id,
                is_private=body.is_private,
            )
        return self._to_profile_response(user)

    async def update_status(
        self,
        *,
        user_id: str,
        body: UpdateStatusRequest,
    ) -> UserProfileResponse:
        expires_at = self._normalize_status_expiry(body.status_expires_at)
        user = await self.users.update_status(
            user_id=user_id,
            status_emoji=_strip_or_none(body.status_emoji),
            status_text=_strip_or_none(body.status_text),
            status_expires_at=expires_at,
        )
        return self._to_profile_response(user)

    async def clear_status(self, *, user_id: str) -> UserProfileResponse:
        user = await self.users.clear_status(user_id=user_id)
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

    def _require_channels(self) -> ChannelsRepository:
        if self.channels is None:
            raise AppError(
                code="CHANNELS_UNAVAILABLE",
                message="Channel access is not configured",
                status_code=500,
            )
        return self.channels

    async def set_main_channel(
        self,
        *,
        user_id: str,
        channel_id: str | None,
    ) -> UserProfileResponse:
        """Pin (or clear) the public channel shown as the profile's main timeline.

        Only the channel's owner (its creator) may pin it, and only public
        channels are eligible so visitors can always view the pinned timeline.
        """
        if channel_id is not None:
            channel = await self._require_channels().get_by_id(
                channel_id, invalid_message="Invalid channel id"
            )
            if channel is None:
                raise AppError(
                    code="CHANNEL_NOT_FOUND",
                    message="Channel not found",
                    status_code=404,
                )
            if (
                channel.owner.type != "user"
                or str(channel.owner.id) != str(user_id)
            ):
                raise AppError(
                    code="CHANNEL_NOT_OWNED",
                    message="Only the channel owner can pin it as main",
                    status_code=403,
                )

        user = await self.users.update_main_channel(
            user_id=user_id, channel_id=channel_id
        )
        return self._to_profile_response(user)

    async def list_user_channels(
        self,
        *,
        user_id: str,
        limit: int = MAX_PROFILE_CHANNELS,
        offset: int = 0,
    ) -> list[UserChannelView]:
        """Public channels owned by ``user_id``, main channel first.

        Shared by the owner's ``/me`` page and visitors on ``/profile/:id``.
        """
        user = await self.users.find_by_id(user_id)
        if not user:
            raise AppError(
                code="USER_NOT_FOUND", message="User not found", status_code=404
            )

        channels_repo = self._require_channels()
        capped = max(1, min(limit, MAX_PROFILE_CHANNELS))
        channels = await channels_repo.list_by_owner(
            owner_type="user",
            owner_id=user_id,
            limit=capped,
            skip=max(0, offset),
        )

        main_channel_id = _doc_value(user, "main_channel_id")
        views = [
            self._to_channel_view(channel, main_channel_id=main_channel_id)
            for channel in channels
        ]
        # Surface the pinned main channel first; the rest keep newest-first order.
        views.sort(key=lambda view: not view.is_main)
        return views

    def _to_channel_view(
        self, channel: ChannelDocument, *, main_channel_id: str | None
    ) -> UserChannelView:
        channel_id = _doc_value(channel, "id", "")
        return UserChannelView(
            id=channel_id,
            title=_doc_value(channel, "name"),
            slug=_doc_value(channel, "slug"),
            description=_doc_value(channel, "description"),
            visibility=_doc_value(channel, "visibility"),
            posting_policy=_doc_value(channel, "posting_policy"),
            read_policy=(
                "public"
                if _doc_value(channel, "visibility") == "public"
                else "members"
            ),
            member_count=_doc_value(channel, "follower_count", 0),
            last_message_at=_doc_value(channel, "last_activity_at"),
            created_at=_doc_value(channel, "created_at"),
            is_main=main_channel_id is not None
            and str(channel_id) == str(main_channel_id),
        )

    def _to_profile_response(self, user: UserDocument) -> UserProfileResponse:
        user = self._without_expired_status(user)
        return UserProfileResponse(
            id=_doc_value(user, "id", ""),
            email=_doc_value(user, "email"),
            is_verified=_doc_value(user, "is_verified"),
            username=_doc_value(user, "username"),
            display_name=_doc_value(user, "display_name"),
            bio=_doc_value(user, "bio"),
            avatar=build_user_avatar_payload(_doc_value(user, "avatar")),
            is_private=_doc_value(user, "is_private"),
            is_bot=_doc_value(user, "is_bot", False),
            main_channel_id=_doc_value(user, "main_channel_id"),
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
        self,
        user: UserDocument,
        *,
        relationship: ContactState,
        include_private_profile: bool,
        extras: ContactExtras | None = None,
        shares_context: bool = False,
    ) -> SelectedUserProfileResponse:
        user = self._without_expired_status(user)
        user_id = _doc_value(user, "id", "")
        presence_state: PresenceState = "offline"
        if (include_private_profile or shares_context) and self.presence_service:
            presence_state = await self.presence_service.get_state(user_id)
        is_online = presence_state != "offline"
        return SelectedUserProfileResponse(
            id=user_id,
            username=_doc_value(user, "username"),
            display_name=_doc_value(user, "display_name"),
            bio=_doc_value(user, "bio") if include_private_profile else None,
            avatar=build_user_avatar_payload(_doc_value(user, "avatar")),
            is_bot=_doc_value(user, "is_bot", False),
            main_channel_id=_doc_value(user, "main_channel_id"),
            status_emoji=(
                _doc_value(user, "status_emoji") if include_private_profile else None
            ),
            status_text=(
                _doc_value(user, "status_text") if include_private_profile else None
            ),
            status_expires_at=(
                _doc_value(user, "status_expires_at")
                if include_private_profile
                else None
            ),
            pronouns=_doc_value(user, "pronouns") if include_private_profile else None,
            timezone=_doc_value(user, "timezone") if include_private_profile else None,
            is_online=is_online,
            presence_state=presence_state,
            last_seen_at=(
                _doc_value(user, "last_seen_at")
                if include_private_profile and presence_state in {"away", "offline"}
                else None
            ),
            profile_visibility="full" if include_private_profile else "limited",
            relationship=relationship,
            connection_timestamp=extras.connection_timestamp if extras else None,
            conversation_id=extras.conversation_id if extras else None,
            shared_conversations=extras.shared_conversations if extras else [],
            shared_spaces=extras.shared_spaces if extras else [],
        )

    def _normalize_status_expiry(self, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        normalized = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
        if normalized <= datetime.now(UTC):
            raise AppError(
                code="STATUS_EXPIRY_IN_PAST",
                message="status_expires_at must be in the future",
                status_code=400,
            )
        return normalized

    def _status_is_expired(self, user: object) -> bool:
        expires_at = _doc_value(user, "status_expires_at")
        if expires_at is None:
            return False
        normalized = (
            expires_at
            if expires_at.tzinfo is not None
            else expires_at.replace(tzinfo=UTC)
        )
        return normalized <= datetime.now(UTC)

    def _without_expired_status(self, user: Any) -> Any:
        if not self._status_is_expired(user):
            return user
        if isinstance(user, dict):
            user["status_emoji"] = None
            user["status_text"] = None
            user["status_expires_at"] = None
            return user
        user.status_emoji = None
        user.status_text = None
        user.status_expires_at = None
        return user

    async def _clear_expired_status_if_needed(self, user: UserDocument) -> UserDocument:
        if not self._status_is_expired(user):
            return user
        return await self.users.clear_status(user_id=_doc_value(user, "id", ""))

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
