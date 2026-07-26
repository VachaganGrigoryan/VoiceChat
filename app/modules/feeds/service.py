from __future__ import annotations

from app.core.errors import AppError
from app.db.models import ChannelDocument
from app.modules.auth.repository import UsersRepository
from app.modules.authorization import AuthorizationService
from app.modules.authorization.permissions import MESSAGE_READ
from app.modules.channels.repository import ChannelsRepository
from app.modules.feeds.schemas import FeedAuthor, FeedPostView
from app.modules.messages.schemas import MessageDoc
from app.modules.messages.service import MessagesService
from app.modules.users.avatar import build_user_avatar_payload


class FeedsService:
    """Read-only channel and profile-feed projections over unified messages."""

    def __init__(
        self,
        *,
        channels_repo: ChannelsRepository,
        messages_service: MessagesService,
        users_repo: UsersRepository,
        authorization: AuthorizationService | None = None,
    ) -> None:
        self.channels_repo = channels_repo
        self.messages = messages_service
        self.users_repo = users_repo
        self.authorization = authorization or AuthorizationService()

    async def _load_channel(self, channel_id: str) -> ChannelDocument:
        channel = await self.channels_repo.get_by_id(
            channel_id, invalid_message="Invalid channel id"
        )
        if channel is None:
            raise AppError(
                code="CHANNEL_NOT_FOUND", message="Channel not found", status_code=404
            )
        return channel

    async def _can_view_channel(
        self, *, viewer_id: str, channel: ChannelDocument
    ) -> bool:
        return await self.authorization.can(
            viewer_id,
            MESSAGE_READ,
            "channel",
            channel.str_id,
        )

    async def assert_can_view_channel(
        self, *, viewer_id: str, channel: ChannelDocument
    ) -> None:
        if not await self._can_view_channel(viewer_id=viewer_id, channel=channel):
            raise AppError(
                code="FEED_FORBIDDEN",
                message="You do not have access to this channel's posts",
                status_code=403,
            )

    async def list_channel_posts(
        self, *, viewer_id: str, channel_id: str, limit: int, cursor: str | None
    ) -> tuple[list[FeedPostView], str | None]:
        channel = await self._load_channel(channel_id)
        await self.assert_can_view_channel(viewer_id=viewer_id, channel=channel)
        docs, next_cursor = await self.messages.get_history(
            container_type="channel",
            container_id=channel.str_id,
            user_id=viewer_id,
            limit=limit,
            cursor=cursor,
        )
        return await self._to_feed_posts(docs), next_cursor

    async def list_channel_post_comments(
        self, *, viewer_id: str, channel_id: str, post_id: str
    ) -> list[FeedPostView]:
        channel = await self._load_channel(channel_id)
        await self.assert_can_view_channel(viewer_id=viewer_id, channel=channel)
        docs = await self.messages.get_thread(
            container_type="channel",
            container_id=channel.str_id,
            message_id=post_id,
            user_id=viewer_id,
        )
        return await self._to_feed_posts(docs)

    async def list_user_feed(
        self, *, viewer_id: str, owner_id: str, limit: int, cursor: str | None
    ) -> tuple[list[FeedPostView], str | None]:
        user = await self.users_repo.find_by_id(owner_id)
        if user is None:
            raise AppError(
                code="USER_NOT_FOUND", message="User not found", status_code=404
            )
        if user.main_channel_id is None:
            return [], None
        return await self.list_channel_posts(
            viewer_id=viewer_id,
            channel_id=user.main_channel_id,
            limit=limit,
            cursor=cursor,
        )

    async def list_profile_posts(
        self,
        *,
        viewer_id: str,
        username: str,
        limit: int,
        cursor: str | None,
    ) -> tuple[list[FeedPostView], str | None]:
        user = await self.users_repo.find_by_username(username)
        if user is None:
            raise AppError(
                code="USER_NOT_FOUND", message="User not found", status_code=404
            )
        if user.main_channel_id is None:
            return [], None
        return await self.list_channel_posts(
            viewer_id=viewer_id,
            channel_id=user.main_channel_id,
            limit=limit,
            cursor=cursor,
        )

    async def _to_feed_posts(self, docs: list[MessageDoc]) -> list[FeedPostView]:
        if not docs:
            return []
        author_ids = [str(doc.sender_id) for doc in docs]
        users = await self.users_repo.find_by_ids(author_ids)
        return [self._to_feed_post(doc, users) for doc in docs]

    def _to_feed_post(self, doc: MessageDoc, users: dict) -> FeedPostView:
        plaintext = doc.content.plaintext if doc.content else None
        text = plaintext.text if plaintext else None
        attachments = list(doc.content.attachments) if doc.content else []
        if plaintext and plaintext.media is not None:
            attachments = [plaintext.media, *attachments]

        author_doc = users.get(str(doc.sender_id))
        author = FeedAuthor(
            id=str(doc.sender_id),
            username=getattr(author_doc, "username", None) if author_doc else None,
            display_name=(
                getattr(author_doc, "display_name", None) if author_doc else None
            ),
            avatar=(
                build_user_avatar_payload(getattr(author_doc, "avatar", None))
                if author_doc
                else None
            ),
        )
        return FeedPostView(
            id=str(doc.id),
            channel_id=str(doc.container_id),
            author=author,
            type=doc.type,
            text=text,
            attachments=attachments,
            reactions=doc.reactions,
            comment_count=doc.thread_reply_count,
            has_thread=doc.is_thread_root,
            is_deleted=doc.is_deleted,
            created_at=doc.created_at,
            edited_at=doc.edited_at,
        )
