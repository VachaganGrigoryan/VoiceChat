from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal, Protocol

from app.core.errors import AppError
from app.db.models import ChannelDocument, UserDocument
from app.db.models.embedded import OwnerRef
from app.modules.authorization import AuthorizationService
from app.modules.authorization.permissions import (
    MESSAGE_READ,
    RESOURCE_MANAGE,
    RESOURCE_VIEW,
)
from app.modules.channels.repository import ChannelsRepository
from app.modules.channels.schemas import (
    ChannelCommentPolicy,
    ChannelJoinPolicy,
    ChannelKind,
    ChannelPostingPolicy,
    ChannelView,
    ChannelVisibility,
)
from app.modules.messages.schemas import MessageDoc, ReplyMode
from app.modules.messages.service import SendMessageResult


class MessagesServiceProto(Protocol):
    async def get_history(
        self,
        *,
        container_type: Literal["channel"],
        container_id: str,
        user_id: str,
        limit: int,
        cursor: str | None,
    ) -> tuple[list[MessageDoc], str | None]: ...

    async def send_text(
        self,
        *,
        container_type: Literal["channel"],
        container_id: str,
        sender_id: str,
        text: str,
        reply_mode: ReplyMode | None = None,
        reply_to_message_id: str | None = None,
    ) -> SendMessageResult: ...


class UsersRepositoryProto(Protocol):
    async def find_by_id(self, user_id: str) -> UserDocument | None: ...


class ChannelService:
    def __init__(
        self,
        *,
        repo: ChannelsRepository,
        messages: MessagesServiceProto | None = None,
        users: UsersRepositoryProto | None = None,
        authorization: AuthorizationService | None = None,
    ) -> None:
        self.repo = repo
        self.messages = messages
        self.users = users
        self.authorization = authorization or AuthorizationService()

    async def create(
        self,
        *,
        created_by: str,
        name: str,
        slug: str,
        kind: ChannelKind = "text",
        description: str | None = None,
        visibility: ChannelVisibility = "public",
        join_policy: ChannelJoinPolicy = "open",
        posting_policy: ChannelPostingPolicy = "everyone",
        comment_policy: ChannelCommentPolicy = "everyone",
        tags: list[str] | None = None,
        owner_type: Literal["user", "space"] = "user",
        owner_id: str | None = None,
        space_id: str | None = None,
    ) -> ChannelView:
        owner_id = str(owner_id or created_by)
        now = datetime.now(UTC)
        channel = ChannelDocument(
            owner=OwnerRef(type=owner_type, id=owner_id),
            space_id=space_id,
            kind=kind,
            slug=slug.strip().lower(),
            name=name.strip(),
            description=self._strip_or_none(description),
            visibility=visibility,
            join_policy=join_policy,
            posting_policy=posting_policy,
            comment_policy=comment_policy,
            tags=self._normalize_tags(tags or []),
            created_by=str(created_by),
            created_at=now,
            updated_at=now,
        )
        return self.to_view(await self.repo.insert(channel))

    async def ensure_profile_channel(
        self,
        *,
        user_id: str,
        username: str,
        is_private: bool = False,
    ) -> ChannelDocument:
        existing = await self.repo.get_profile_channel(user_id=user_id)
        if existing is not None:
            return existing

        now = datetime.now(UTC)
        channel = ChannelDocument(
            owner=OwnerRef(type="user", id=str(user_id)),
            kind="profile",
            slug="feed",
            name=f"{username}'s feed",
            visibility="members" if is_private else "public",
            join_policy="approval" if is_private else "open",
            posting_policy="owner",
            comment_policy="everyone",
            created_by=str(user_id),
            created_at=now,
            updated_at=now,
        )
        try:
            return await self.repo.insert(channel)
        except AppError as exc:
            if exc.code != "CHANNEL_SLUG_TAKEN":
                raise
            winner = await self.repo.get_profile_channel(user_id=user_id)
            if winner is None:
                raise
            return winner

    async def get(self, *, channel_id: str, viewer_id: str) -> ChannelView:
        channel = await self._get(channel_id)
        await self.authorization.require(
            viewer_id,
            RESOURCE_VIEW,
            "channel",
            channel.str_id,
            message="Not allowed to view this channel",
        )
        return self.to_view(channel)

    async def update(
        self,
        *,
        channel_id: str,
        actor_user_id: str,
        updates: dict[str, Any],
    ) -> ChannelView:
        channel = await self._get(channel_id)
        await self.authorization.require(
            actor_user_id,
            RESOURCE_MANAGE,
            "channel",
            channel.str_id,
            message="Not allowed to manage this channel",
        )
        clearable = {"description", "avatar", "banner"}
        normalized = {
            key: value
            for key, value in updates.items()
            if value is not None or key in clearable
        }
        if "name" in normalized:
            normalized["name"] = normalized["name"].strip()
        if "description" in normalized:
            normalized["description"] = self._strip_or_none(normalized["description"])
        if "tags" in normalized:
            normalized["tags"] = self._normalize_tags(normalized["tags"])
        updated = await self.repo.update_by_id(
            channel_id=channel.str_id, updates=normalized
        )
        if updated is None:
            raise self._not_found()
        return self.to_view(updated)

    async def set_profile_privacy(
        self, *, channel_id: str, is_private: bool
    ) -> ChannelDocument:
        updated = await self.repo.update_by_id(
            channel_id=channel_id,
            updates={
                "visibility": "members" if is_private else "public",
                "join_policy": "approval" if is_private else "open",
            },
        )
        if updated is None or updated.kind != "profile":
            raise self._not_found()
        return updated

    async def list_messages(
        self,
        *,
        channel_id: str,
        viewer_id: str,
        limit: int,
        cursor: str | None,
    ) -> tuple[list[MessageDoc], str | None]:
        channel = await self._get(channel_id)
        await self.authorization.require(
            viewer_id,
            MESSAGE_READ,
            "channel",
            channel.str_id,
            message="Not allowed to read this channel",
        )
        messages = self._require_messages()
        return await messages.get_history(
            container_type="channel",
            container_id=channel.str_id,
            user_id=viewer_id,
            limit=limit,
            cursor=cursor,
        )

    async def create_message(
        self,
        *,
        channel_id: str,
        sender_id: str,
        text: str,
        reply_mode: ReplyMode | None = None,
        reply_to_message_id: str | None = None,
    ) -> MessageDoc:
        channel = await self._get(channel_id)
        result = await self._require_messages().send_text(
            container_type="channel",
            container_id=channel.str_id,
            sender_id=sender_id,
            text=text,
            reply_mode=reply_mode,
            reply_to_message_id=reply_to_message_id,
        )
        return result.message

    async def create_profile_message(
        self,
        *,
        user_id: str,
        text: str,
        reply_mode: ReplyMode | None = None,
        reply_to_message_id: str | None = None,
    ) -> MessageDoc:
        user = await self._require_users().find_by_id(user_id)
        if user is None:
            raise AppError(
                code="USER_NOT_FOUND", message="User not found", status_code=404
            )
        if user.main_channel_id is None:
            raise AppError(
                code="PROFILE_CHANNEL_NOT_FOUND",
                message="Profile channel not found",
                status_code=404,
            )
        return await self.create_message(
            channel_id=user.main_channel_id,
            sender_id=user_id,
            text=text,
            reply_mode=reply_mode,
            reply_to_message_id=reply_to_message_id,
        )

    async def _get(self, channel_id: str) -> ChannelDocument:
        channel = await self.repo.get_by_id(
            channel_id, invalid_message="Invalid channel id"
        )
        if channel is None:
            raise self._not_found()
        return channel

    def _require_messages(self) -> MessagesServiceProto:
        if self.messages is None:
            raise RuntimeError("Messages service is required for channel content")
        return self.messages

    def _require_users(self) -> UsersRepositoryProto:
        if self.users is None:
            raise RuntimeError("Users repository is required for profile posts")
        return self.users

    @staticmethod
    def _strip_or_none(value: str | None) -> str | None:
        normalized = (value or "").strip()
        return normalized or None

    @staticmethod
    def _normalize_tags(tags: list[str]) -> list[str]:
        return list(dict.fromkeys(tag.strip().lower() for tag in tags if tag.strip()))

    @staticmethod
    def _not_found() -> AppError:
        return AppError(
            code="CHANNEL_NOT_FOUND", message="Channel not found", status_code=404
        )

    @staticmethod
    def to_view(channel: ChannelDocument) -> ChannelView:
        return ChannelView(
            id=channel.str_id,
            owner={"type": channel.owner.type, "id": str(channel.owner.id)},
            space_id=str(channel.space_id) if channel.space_id is not None else None,
            kind=channel.kind,
            slug=channel.slug,
            name=channel.name,
            description=channel.description,
            avatar=channel.avatar,
            banner=channel.banner,
            visibility=channel.visibility,
            join_policy=channel.join_policy,
            posting_policy=channel.posting_policy,
            comment_policy=channel.comment_policy,
            tags=channel.tags,
            message_count=channel.message_count,
            follower_count=channel.follower_count,
            last_message_id=channel.last_message_id,
            last_activity_at=channel.last_activity_at,
            legacy_conversation_id=channel.legacy_conversation_id,
            created_by=str(channel.created_by),
            created_at=channel.created_at,
            updated_at=channel.updated_at,
        )
