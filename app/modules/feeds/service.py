from __future__ import annotations

from app.core.errors import AppError
from app.db.models import ConversationDocument
from app.modules.auth.repository import UsersRepository
from app.modules.conversations.repository import ConversationsRepository
from app.modules.feeds.schemas import FeedAuthor, FeedPostView
from app.modules.messages.repository import MessagesRepository
from app.modules.messages.repository.mappers import to_message_doc
from app.modules.messages.schemas import MessageDoc
from app.modules.messages.service import MessagesService
from app.modules.pings.service import PingsService
from app.modules.users.avatar import build_user_avatar_payload

# Bound how many of a user's channels the aggregate feed spans.
MAX_FEED_CHANNELS = 50


class FeedsService:
    """Read-only access to channel posts, enforcing the channel ``read_policy``
    rather than membership so a profile's public channels are visible to their
    intended audience (contacts / everyone) without joining."""

    def __init__(
        self,
        *,
        conversations_repo: ConversationsRepository,
        messages_service: MessagesService,
        messages_repo: MessagesRepository,
        pings_service: PingsService,
        users_repo: UsersRepository,
    ) -> None:
        self.conversations_repo = conversations_repo
        self.messages = messages_service
        self.messages_repo = messages_repo
        self.pings = pings_service
        self.users_repo = users_repo

    async def _load_channel(self, channel_id: str) -> ConversationDocument:
        channel = await self.conversations_repo.get_by_id(
            channel_id, invalid_message="Invalid channel id"
        )
        if channel is None or channel.type != "channel":
            raise AppError(
                code="CHANNEL_NOT_FOUND", message="Channel not found", status_code=404
            )
        return channel

    async def _can_view_channel(
        self, *, viewer_id: str, channel: ConversationDocument
    ) -> bool:
        if str(channel.created_by) == str(viewer_id):
            return True
        participant = await self.conversations_repo.get_participant(
            conversation_id=channel.str_id, user_id=viewer_id
        )
        if participant is not None:
            return True

        policy = getattr(channel, "read_policy", "members")
        if policy == "public":
            return True
        if policy == "contacts":
            return await self.pings.has_chat_permission(
                user_a=viewer_id, user_b=str(channel.created_by)
            )
        return False

    async def assert_can_view_channel(
        self, *, viewer_id: str, channel: ConversationDocument
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
        docs, next_cursor = await self.messages.get_conversation_history(
            conversation_id=channel.str_id,
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
        docs = await self.messages.get_thread_for_conversation(
            conversation_id=channel.str_id,
            message_id=post_id,
            user_id=viewer_id,
        )
        return await self._to_feed_posts(docs)

    async def list_user_feed(
        self, *, viewer_id: str, owner_id: str, limit: int, cursor: str | None
    ) -> tuple[list[FeedPostView], str | None]:
        channels = await self.conversations_repo.list_public_channels_by_creator(
            creator_id=owner_id, limit=MAX_FEED_CHANNELS, skip=0
        )
        viewable_ids = [
            channel.str_id
            for channel in channels
            if await self._can_view_channel(viewer_id=viewer_id, channel=channel)
        ]
        if not viewable_ids:
            return [], None

        docs, next_cursor = await self.messages_repo.list_feed_for_conversations(
            conversation_ids=viewable_ids, limit=limit, cursor=cursor
        )
        mapped = [to_message_doc(doc) for doc in docs]
        return await self._to_feed_posts(mapped), next_cursor

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
            channel_id=str(doc.conversation_id),
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
