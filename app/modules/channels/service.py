from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal, Protocol

from app.core.errors import AppError
from app.db.models import ChannelDocument, UserDocument
from app.db.models.embedded import OwnerRef, TextStyleDocument
from app.modules.authorization import AuthorizationService
from app.modules.authorization.permissions import (
    MESSAGE_CREATE,
    MESSAGE_PIN,
    MESSAGE_READ,
    RESOURCE_DELETE,
    RESOURCE_MANAGE,
    RESOURCE_VIEW,
    THREAD_REPLY,
)
from app.modules.channels.repository import ChannelsRepository
from app.modules.channels.schemas import (
    ChannelCommentPolicy,
    ChannelJoinPolicy,
    ChannelKind,
    ChannelPostingPolicy,
    ChannelSummary,
    ChannelView,
    ChannelVisibility,
    ViewerBlock,
)
from app.modules.messages.schemas import ReplyMode
from app.modules.messages.service import SendMessageResult
from app.modules.relationships.repository import RelationshipsRepository

# Mirrors the write allowlist in relationships/memberships.py, narrowed to the
# fields a channel reader may set on their own edge.
_VIEWER_STATE_FIELDS: frozenset[str] = frozenset(
    {"pinned", "archived", "folder", "muted_until", "notification_level"}
)
# Fields whose meaningful value is null (unpin from a folder, unmute).
_CLEARABLE_STATE: frozenset[str] = frozenset({"folder", "muted_until"})
_EPOCH = datetime.min.replace(tzinfo=UTC)


class MessagesServiceProto(Protocol):
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
        relationships: RelationshipsRepository | None = None,
        authorization: AuthorizationService | None = None,
    ) -> None:
        self.repo = repo
        self.messages = messages
        self.users = users
        self.relationships = relationships or RelationshipsRepository()
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
        if space_id is None:
            from app.db.models import SpaceDocument

            vogi_space = await SpaceDocument.find_one({"slug": "vogi"})
            if vogi_space is not None:
                space_id = vogi_space.str_id
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
        view = self.to_view(channel)
        view.viewer = await self.viewer_block(
            channel_id=channel.str_id, viewer_id=viewer_id
        )
        return view

    async def viewer_block(self, *, channel_id: str, viewer_id: str) -> ViewerBlock:
        """The caller's first-paint affordances for one channel.

        Every field comes from the same decision function the capabilities
        endpoint uses, so the two cannot disagree. Membership and follow are
        descriptive, so they are read directly.
        """
        membership = await self.relationships.find_edge(
            kind="membership",
            user_id=viewer_id,
            target_type="channel",
            target_id=channel_id,
        )
        follow = await self.relationships.find_edge(
            kind="follow",
            user_id=viewer_id,
            target_type="channel",
            target_id=channel_id,
        )
        return ViewerBlock(
            can_post=await self.authorization.can(
                viewer_id, MESSAGE_CREATE, "channel", channel_id
            ),
            can_comment=await self.authorization.can(
                viewer_id, THREAD_REPLY, "channel", channel_id
            ),
            can_manage=await self.authorization.can(
                viewer_id, RESOURCE_MANAGE, "channel", channel_id
            ),
            membership_status=membership.status if membership is not None else None,
            is_follower=follow is not None and follow.status == "active",
        )

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

    async def create_message(
        self,
        *,
        channel_id: str,
        sender_id: str,
        text: str,
        reply_mode: ReplyMode | None = None,
        reply_to_message_id: str | None = None,
        style: TextStyleDocument | None = None,
    ) -> SendMessageResult:
        """Returns the full send result so the caller can fan it out over realtime.

        Returning only ``result.message`` previously made it impossible to emit a
        ``thread_reply_created`` + ``thread_summary_updated`` pair, so channel
        messages never reached subscribers live.
        """
        channel = await self._get(channel_id)
        return await self._require_messages().send_text(
            container_type="channel",
            container_id=channel.str_id,
            sender_id=sender_id,
            text=text,
            reply_mode=reply_mode,
            reply_to_message_id=reply_to_message_id,
            style=style,
        )

    async def delete_channel(self, *, channel_id: str, actor_user_id: str) -> list[str]:
        """Hard-deletes a channel and everything that named it.

        Returns the audience to notify, snapshotted before the cascade because
        it is read from the membership and follow records being deleted.
        """
        channel = await self._get(channel_id)
        await self.authorization.require(
            actor_user_id,
            RESOURCE_DELETE,
            "channel",
            channel.str_id,
            message="Not allowed to delete this channel",
        )

        from app.modules.authorization.capabilities import affected_viewer_ids
        from app.modules.resources import ResourceCascade

        recipients = await affected_viewer_ids(
            resource_type="channel", resource_id=channel.str_id
        )
        report = await ResourceCascade().delete_channel_tree(channel)

        from app.db.models import AuditLogDocument

        await AuditLogDocument(
            actor_id=actor_user_id,
            action="delete_channel",
            target_type="channel",
            target_id=channel.str_id,
            space_id=channel.space_id,
            data={"removed": report.removed},
        ).insert()
        return recipients

    async def pin_message(
        self, *, channel_id: str, message_id: str, actor_user_id: str
    ) -> ChannelDocument:
        return await self._set_pinned(
            channel_id=channel_id,
            message_id=message_id,
            actor_user_id=actor_user_id,
            add=True,
        )

    async def unpin_message(
        self, *, channel_id: str, message_id: str, actor_user_id: str
    ) -> ChannelDocument:
        return await self._set_pinned(
            channel_id=channel_id,
            message_id=message_id,
            actor_user_id=actor_user_id,
            add=False,
        )

    async def _set_pinned(
        self, *, channel_id: str, message_id: str, actor_user_id: str, add: bool
    ) -> ChannelDocument:
        """Pin or unpin, resolved at the channel's own scope.

        The pin right is decided against the channel resource, so channel roles
        and channel ownership govern it — not the group management right that
        the conversation path asks about.
        """
        channel = await self._get(channel_id)
        await self.authorization.require(
            actor_user_id,
            MESSAGE_PIN,
            "channel",
            channel.str_id,
            message="Not allowed to pin messages in this channel",
        )
        write = self.repo.add_pinned_message if add else self.repo.remove_pinned_message
        updated = await write(channel_id=channel.str_id, message_id=message_id)
        if updated is None:
            raise self._not_found()
        return updated

    async def list_for_inbox(
        self, *, user_id: str, limit: int = 100
    ) -> list[dict[str, Any]]:
        """Channels the caller belongs to, with their own state and unread count.

        This is the channel analogue of the conversation inbox: one row per
        channel carrying enough per-user state for the sidebar to render it
        exactly like a DM or group.
        """
        edges = [
            *await self.relationships.list_for_user(
                kind="membership",
                user_id=user_id,
                target_type="channel",
                status="active",
                limit=limit,
            ),
            *await self.relationships.list_for_user(
                kind="follow",
                user_id=user_id,
                target_type="channel",
                status="active",
                limit=limit,
            ),
        ]
        # A user can both follow and be a member; membership is listed first so
        # dict insertion keeps it as the authoritative edge.
        edge_by_channel: dict[str, Any] = {}
        for edge in edges:
            edge_by_channel.setdefault(str(edge.target_id), edge)

        if not edge_by_channel:
            return []

        channels = await self.repo.list_by_ids(list(edge_by_channel.keys()))
        rows: list[dict[str, Any]] = []
        for channel in channels:
            edge = edge_by_channel.get(channel.str_id)
            if edge is None:
                continue
            rows.append(
                {
                    "channel": self.to_view(channel),
                    "state": self._state_view(edge),
                    "unread_count": await self._unread_count(
                        channel=channel,
                        last_read_at=edge.state.last_read_at,
                        user_id=user_id,
                    ),
                    "joined": edge.kind == "membership",
                }
            )

        rows.sort(
            key=lambda row: row["channel"].last_activity_at or _EPOCH, reverse=True
        )
        return rows

    async def _unread_count(
        self,
        *,
        channel: ChannelDocument,
        last_read_at: datetime | None,
        user_id: str,
    ) -> int:
        """Messages posted after the caller last read the channel."""
        if channel.last_activity_at is None:
            return 0
        if last_read_at is None:
            return channel.message_count
        if last_read_at >= channel.last_activity_at:
            return 0
        return await self.repo.count_messages_after(
            channel_id=channel.str_id, after=last_read_at, user_id=user_id
        )

    async def leave(self, *, channel_id: str, user_id: str) -> bool:
        """Revoke the caller's membership. Following is managed separately."""
        channel = await self._get(channel_id)
        edge = await self.relationships.find_edge(
            kind="membership",
            user_id=user_id,
            target_type="channel",
            target_id=channel.str_id,
        )
        # find_edge ignores status, so a previously revoked membership still
        # resolves; only an active one can be left.
        if edge is None or edge.status != "active":
            raise AppError(
                code="CHANNEL_NOT_JOINED",
                message="You are not a member of this channel",
                status_code=409,
            )
        # Revoke rather than delete so the membership history survives, matching
        # how MembershipService.revoke treats every other target type.
        await self.relationships.set_status(
            relationship_id=edge.str_id,
            status="revoked",
            expected_status="active",
        )
        return True

    async def list_members(self, *, channel_id: str, viewer_id: str) -> list[Any]:
        channel = await self._get(channel_id)
        await self.authorization.require(
            viewer_id,
            RESOURCE_VIEW,
            "channel",
            channel.str_id,
            message="Not allowed to view this channel",
        )
        return await self.relationships.list_for_target(
            kind="membership",
            target_type="channel",
            target_id=channel.str_id,
            status="active",
            limit=500,
        )

    async def _viewer_edge(self, *, channel_id: str, user_id: str):
        """The caller's relationship to a channel.

        A reader may be a member or only a follower; both carry the same
        ``RelationshipState`` bag, so per-user channel state resolves against
        whichever edge exists.
        """
        edge = await self.relationships.find_edge(
            kind="membership",
            user_id=user_id,
            target_type="channel",
            target_id=channel_id,
        )
        if edge is not None:
            return edge
        return await self.relationships.find_edge(
            kind="follow",
            user_id=user_id,
            target_type="channel",
            target_id=channel_id,
        )

    async def update_viewer_state(
        self,
        *,
        channel_id: str,
        user_id: str,
        updates: dict[str, Any],
    ) -> dict[str, Any]:
        """Patch the caller's own inbox state (pin/archive/folder/notifications)."""
        channel = await self._get(channel_id)
        await self.authorization.require(
            user_id,
            RESOURCE_VIEW,
            "channel",
            channel.str_id,
            message="Not allowed to view this channel",
        )
        edge = await self._viewer_edge(channel_id=channel.str_id, user_id=user_id)
        if edge is None or edge.status != "active":
            raise AppError(
                code="CHANNEL_NOT_JOINED",
                message="Join or follow this channel before changing its settings",
                status_code=409,
            )
        allowed = {
            key: value
            for key, value in updates.items()
            if key in _VIEWER_STATE_FIELDS
            and value is not None
            or key in _CLEARABLE_STATE
        }
        if not allowed:
            return self._state_view(edge)
        updated = await self.relationships.update_state(
            relationship_id=edge.str_id, updates=allowed
        )
        return self._state_view(updated or edge)

    async def mark_channel_read(
        self, *, channel_id: str, user_id: str
    ) -> dict[str, Any]:
        """Mark the whole channel read up to its latest message."""
        channel = await self._get(channel_id)
        await self.authorization.require(
            user_id,
            MESSAGE_READ,
            "channel",
            channel.str_id,
            message="Not allowed to read this channel",
        )
        edge = await self._viewer_edge(channel_id=channel.str_id, user_id=user_id)
        if edge is None or edge.status != "active":
            raise AppError(
                code="CHANNEL_NOT_JOINED",
                message="Join or follow this channel before marking it read",
                status_code=409,
            )
        updated = await self.relationships.update_state(
            relationship_id=edge.str_id,
            updates={
                "last_read_at": datetime.now(UTC),
                "last_read_message_id": channel.last_message_id,
            },
        )
        return self._state_view(updated or edge)

    async def advance_read_cursor(
        self, *, channel_id: str, user_id: str, last_read_message_id: str
    ) -> None:
        """Move a viewer's read cursor to one specific message.

        The channel counterpart of marking a conversation read at a message: a
        channel's per-viewer read state lives on the follow edge. A reader with
        no edge — a stranger reading a public channel — has no cursor to move,
        which is not an error.
        """
        channel = await self._get(channel_id)
        await self.authorization.require(
            user_id,
            MESSAGE_READ,
            "channel",
            channel.str_id,
            message="Not allowed to read this channel",
        )
        edge = await self._viewer_edge(channel_id=channel.str_id, user_id=user_id)
        if edge is None:
            return
        await self.relationships.update_state(
            relationship_id=edge.str_id,
            updates={
                "last_read_at": datetime.now(UTC),
                "last_read_message_id": last_read_message_id,
            },
        )

    @staticmethod
    def _state_view(edge: Any) -> dict[str, Any]:
        state = edge.state
        return {
            "channel_id": str(edge.target_id),
            "pinned": state.pinned,
            "archived": state.archived,
            "folder": state.folder,
            "muted_until": state.muted_until,
            "notification_level": state.notification_level,
            "last_read_message_id": state.last_read_message_id,
        }

    async def create_profile_message(
        self,
        *,
        user_id: str,
        text: str,
        reply_mode: ReplyMode | None = None,
        reply_to_message_id: str | None = None,
    ) -> SendMessageResult:
        """A profile post is a message in the user's own channel, so it returns the
        same send result and fans out through the same path as any channel message."""
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
    def to_summary(
        channel: ChannelDocument, *, is_main: bool = False
    ) -> ChannelSummary:
        """The list-context projection every channel listing returns."""
        return ChannelSummary(
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
            tags=channel.tags,
            message_count=channel.message_count,
            follower_count=channel.follower_count,
            last_activity_at=channel.last_activity_at,
            is_main=is_main,
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
            pinned_message_ids=[str(mid) for mid in channel.pinned_message_ids],
            last_activity_at=channel.last_activity_at,
            legacy_conversation_id=channel.legacy_conversation_id,
            created_by=str(channel.created_by),
            created_at=channel.created_at,
            updated_at=channel.updated_at,
        )
