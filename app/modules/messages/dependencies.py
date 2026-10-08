from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from fastapi import Depends

from app.core.errors import AppError
from app.core.security import require_verified_user
from app.db.models import (
    ChannelDocument,
    ConversationDocument,
    MessageContainerType,
    MessageDocument,
    UserDocument,
)
from app.modules.authorization.permissions import MESSAGE_READ, RESOURCE_MANAGE
from app.modules.authorization.service import AuthorizationService
from app.modules.conversations.repository import ConversationsRepository
from app.modules.messages.repository import MessagesRepository
from app.modules.messages.service import MessagesService
from app.modules.channels.repository import ChannelsRepository
from app.modules.auth.repository import UsersRepository
from app.modules.conversations.dependencies import get_conversations_service
from app.modules.conversations.service import ConversationsService
from app.modules.relationships.dependencies import get_connection_service


def get_authorization_service() -> AuthorizationService:
    return AuthorizationService()


def get_messages_service() -> MessagesService:
    users_repo = UsersRepository()
    conversations_service = ConversationsService(
        repo=ConversationsRepository(),
        connection_service=get_connection_service(),
        users_repo=users_repo,
    )

    return MessagesService(
        repo=MessagesRepository(),
        conversations_service=conversations_service,
        channels_repo=ChannelsRepository(),
    )


@dataclass
class MessageContext:
    message: MessageDocument
    container_type: Literal["conversation", "channel"]
    container_id: str
    #: Resolved for conversation containers only, for the fan-out helpers that
    #: need a participant roster. Its absence is not a reason to refuse an
    #: operation: a channel is a container like any other.
    conversation: ConversationDocument | None = None


async def require_message_access(
    message_id: str,
    user: UserDocument = Depends(require_verified_user),
    messages_repo: MessagesRepository = Depends(MessagesRepository),
    conversations_repo: ConversationsRepository = Depends(ConversationsRepository),
    authz: AuthorizationService = Depends(get_authorization_service),
) -> MessageContext:
    user_id = user.str_id
    message = await messages_repo.get_by_id(message_id=message_id)
    if message is None:
        raise AppError(
            code="MESSAGE_NOT_FOUND",
            message="Message not found",
            status_code=404,
        )

    await authz.require(
        user_id,
        MESSAGE_READ,
        message.container_type,
        message.container_id,
        message="Not allowed to read this message",
    )

    conversation: ConversationDocument | None = None
    if message.container_type == "conversation":
        conversation = await conversations_repo.get_by_id(message.container_id)

    return MessageContext(
        message=message,
        container_type=message.container_type,  # type: ignore[arg-type]
        container_id=message.container_id,
        conversation=conversation,
    )


@dataclass
class ContainerContext:
    """A resolved message container, whichever kind it is.

    The two kinds keep their own document because their gates differ and their
    pinned sets live in different places; everything downstream of the gate
    reads only ``container_type``/``container_id``.
    """

    container_type: MessageContainerType
    container_id: str
    conversation: ConversationDocument | None = None
    channel: ChannelDocument | None = None

    @property
    def pinned_message_ids(self) -> list[str]:
        container = self.conversation or self.channel
        if container is None:
            return []
        return [str(message_id) for message_id in container.pinned_message_ids]


async def _require_channel(
    container_id: str,
    *,
    channels_repo: ChannelsRepository,
) -> ChannelDocument:
    channel = await channels_repo.get_by_id(
        container_id, invalid_message="Invalid channel id"
    )
    if channel is None:
        raise AppError(
            code="CHANNEL_NOT_FOUND",
            message="Channel not found",
            status_code=404,
        )
    return channel


async def require_container_read(
    container_type: MessageContainerType,
    container_id: str,
    user: UserDocument = Depends(require_verified_user),
    conversations: ConversationsService = Depends(get_conversations_service),
    channels_repo: ChannelsRepository = Depends(ChannelsRepository),
    authz: AuthorizationService = Depends(get_authorization_service),
) -> ContainerContext:
    """Assert the caller may read a container, either kind.

    A conversation gates on membership; a channel gates on its *read policy*, so
    a public channel answers to someone who has not joined it.
    """
    if container_type == "conversation":
        conversation = await conversations.require_participant(
            user_id=user.str_id, conversation_id=container_id
        )
        return ContainerContext(
            container_type=container_type,
            container_id=conversation.str_id,
            conversation=conversation,
        )

    channel = await _require_channel(container_id, channels_repo=channels_repo)
    await authz.require(
        user.str_id,
        MESSAGE_READ,
        "channel",
        channel.str_id,
        message="Not allowed to read this channel",
    )
    return ContainerContext(
        container_type=container_type,
        container_id=channel.str_id,
        channel=channel,
    )


async def require_container_post(
    container_type: MessageContainerType,
    container_id: str,
    user: UserDocument = Depends(require_verified_user),
    conversations: ConversationsService = Depends(get_conversations_service),
    channels_repo: ChannelsRepository = Depends(ChannelsRepository),
) -> ContainerContext:
    """Resolve a container the caller intends to post to.

    A channel is only checked for existence here. Posting and replying are
    distinct rights — a channel may accept comments from an audience it does not
    let post (§59) — and only ``MessagesService._create_message`` knows which of
    the two a request needs, because that follows from ``reply_mode``. Gating on
    `message.create` at the door would refuse a legitimate commenter.
    """
    if container_type == "conversation":
        conversation = await conversations.require_can_post(
            user_id=user.str_id, conversation_id=container_id
        )
        return ContainerContext(
            container_type=container_type,
            container_id=conversation.str_id,
            conversation=conversation,
        )

    channel = await _require_channel(container_id, channels_repo=channels_repo)
    return ContainerContext(
        container_type=container_type,
        container_id=channel.str_id,
        channel=channel,
    )


async def require_container_manage(
    container_type: MessageContainerType,
    container_id: str,
    user: UserDocument = Depends(require_verified_user),
    conversations: ConversationsService = Depends(get_conversations_service),
    channels_repo: ChannelsRepository = Depends(ChannelsRepository),
    authz: AuthorizationService = Depends(get_authorization_service),
) -> ContainerContext:
    """Assert the caller manages a container, for the destructive operations.

    A channel resolves `resource.manage` against itself, so channel roles and
    channel ownership govern it rather than the group-management right the
    conversation path asks about.
    """
    if container_type == "conversation":
        conversation = await conversations.require_group_manager(
            user_id=user.str_id, conversation_id=container_id
        )
        return ContainerContext(
            container_type=container_type,
            container_id=conversation.str_id,
            conversation=conversation,
        )

    channel = await _require_channel(container_id, channels_repo=channels_repo)
    await authz.require(
        user.str_id,
        RESOURCE_MANAGE,
        "channel",
        channel.str_id,
        message="Not allowed to manage this channel",
    )
    return ContainerContext(
        container_type=container_type,
        container_id=channel.str_id,
        channel=channel,
    )
