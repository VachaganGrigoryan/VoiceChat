from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from fastapi import Depends

from app.core.errors import AppError
from app.core.security import require_verified_user
from app.db.models import ConversationDocument, MessageDocument, UserDocument
from app.modules.authorization.permissions import MESSAGE_READ
from app.modules.authorization.service import AuthorizationService
from app.modules.conversations.repository import ConversationsRepository
from app.modules.messages.repository import MessagesRepository
from app.modules.messages.service import MessagesService
from app.modules.channels.repository import ChannelsRepository
from app.modules.auth.repository import UsersRepository
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
    conversation: ConversationDocument | None = None

    def require_conversation_container(self) -> ConversationDocument:
        if self.container_type != "conversation" or self.conversation is None:
            raise AppError(
                code="INVALID_CONTAINER",
                message="This operation is only supported for conversation messages",
                status_code=400,
            )
        return self.conversation


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
