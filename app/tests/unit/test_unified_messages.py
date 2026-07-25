"""Container addressing, flat thread topology, and message authorization.

Covers the `unified-messages` contract (RefactoringPlan §29–37, §91, §97): a
message is addressed by `container_type`/`container_id`, threads and channel
comments are flat topology on that container, and every message action is
decided against the container resource rather than the message.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from beanie import PydanticObjectId
from pydantic import ValidationError

from app.core.errors import AppError
from app.db.models import (
    ChannelDocument,
    ConversationDocument,
    MessageDocument,
    OwnerRef,
    RelationshipDocument,
)
from app.db.models.relationship import RelationshipPermissionOverrides
from app.modules.authorization.permissions import (
    MESSAGE_CREATE,
    MESSAGE_DELETE_OWN,
    MESSAGE_EDIT_OWN,
)
from app.modules.authorization.service import AuthorizationService
from app.modules.messages.repository import MessagesRepository
from app.modules.messages.repository.mappers import to_message_doc, to_thread_summary
from app.modules.messages.service.base import BaseMessagesService

CONVERSATION_ID = "6501f77bd4a1c2b3e4f50100"
OTHER_CONVERSATION_ID = "6501f77bd4a1c2b3e4f50101"
CHANNEL_ID = "6501f77bd4a1c2b3e4f50102"
SENDER = "6501f77bd4a1c2b3e4f50110"
OTHER_USER = "6501f77bd4a1c2b3e4f50111"

ROOT_ID = "6501f77bd4a1c2b3e4f50120"
REPLY_ID = "6501f77bd4a1c2b3e4f50121"


def make_message(
    message_id: str,
    *,
    container_type: str = "conversation",
    container_id: str = CONVERSATION_ID,
    thread_root_id: str | None = None,
    sender_id: str = SENDER,
) -> MessageDocument:
    now = datetime.now(UTC)
    message = MessageDocument(
        container_type=container_type,
        container_id=container_id,
        sender_id=sender_id,
        type="text",
        thread_root_id=thread_root_id,
        created_at=now,
        updated_at=now,
    )
    message.id = PydanticObjectId(message_id)
    return message


# --- 6.1 container + reply/thread invariants ---------------------------------


def test_message_requires_a_container():
    """§97: a message with no valid container is rejected outright."""
    now = datetime.now(UTC)
    with pytest.raises(ValidationError):
        MessageDocument(sender_id=SENDER, created_at=now, updated_at=now)
    with pytest.raises(ValidationError):
        MessageDocument(
            container_type="conversation",
            container_id="",
            sender_id=SENDER,
            created_at=now,
            updated_at=now,
        )


def test_message_container_type_is_conversation_or_channel():
    now = datetime.now(UTC)
    with pytest.raises(ValidationError):
        MessageDocument(
            container_type="thread",
            container_id=CONVERSATION_ID,
            sender_id=SENDER,
            created_at=now,
            updated_at=now,
        )


def test_channel_root_is_a_post_and_thread_reply_is_a_comment():
    """§31–35: Post/Comment are UI words over one storage model."""
    post = make_message(ROOT_ID, container_type="channel", container_id=CHANNEL_ID)
    comment = make_message(
        REPLY_ID,
        container_type="channel",
        container_id=CHANNEL_ID,
        thread_root_id=ROOT_ID,
    )

    assert post.thread_root_id is None
    assert comment.thread_root_id == post.str_id
    assert (comment.container_type, comment.container_id) == (
        post.container_type,
        post.container_id,
    )
    # Both are plain messages: no Post/Comment/Thread document exists.
    assert type(post) is type(comment) is MessageDocument


def test_legacy_row_without_container_reads_as_a_conversation():
    """A row the backfill has not reached still deserializes (design step 5)."""
    now = datetime.now(UTC)
    message = MessageDocument.model_validate(
        {
            "conversation_id": CONVERSATION_ID,
            "sender_id": SENDER,
            "type": "text",
            "created_at": now,
            "updated_at": now,
        }
    )
    assert message.container_type == "conversation"
    assert message.container_id == CONVERSATION_ID


@pytest.mark.asyncio
async def test_cross_container_reply_is_rejected():
    repo = MessagesRepository()
    target = make_message(ROOT_ID, container_id=OTHER_CONVERSATION_ID)

    with patch.object(MessageDocument, "get", new=AsyncMock(return_value=target)):
        with pytest.raises(AppError) as exc:
            await repo._load_reply_target_in_container(
                container_type="conversation",
                container_id=CONVERSATION_ID,
                reply_to_message_id=ROOT_ID,
            )
    assert exc.value.code == "INVALID_REPLY_TARGET"


@pytest.mark.asyncio
async def test_reply_across_container_types_is_rejected():
    """Same id, different container type, is still a different container."""
    repo = MessagesRepository()
    target = make_message(
        ROOT_ID, container_type="channel", container_id=CONVERSATION_ID
    )

    with patch.object(MessageDocument, "get", new=AsyncMock(return_value=target)):
        with pytest.raises(AppError) as exc:
            await repo._load_reply_target_in_container(
                container_type="conversation",
                container_id=CONVERSATION_ID,
                reply_to_message_id=ROOT_ID,
            )
    assert exc.value.code == "INVALID_REPLY_TARGET"


@pytest.mark.asyncio
async def test_same_container_reply_is_accepted():
    repo = MessagesRepository()
    target = make_message(ROOT_ID)

    with patch.object(MessageDocument, "get", new=AsyncMock(return_value=target)):
        loaded = await repo._load_reply_target_in_container(
            container_type="conversation",
            container_id=CONVERSATION_ID,
            reply_to_message_id=ROOT_ID,
        )
    assert loaded.str_id == ROOT_ID


@pytest.mark.asyncio
async def test_thread_root_must_be_in_the_same_container():
    """§36: every item of a thread shares the root's container."""
    repo = MessagesRepository()
    root = make_message(ROOT_ID, container_id=OTHER_CONVERSATION_ID)

    with patch.object(MessageDocument, "get", new=AsyncMock(return_value=root)):
        with pytest.raises(AppError) as exc:
            await repo._load_thread_root_in_container(
                container_type="conversation",
                container_id=CONVERSATION_ID,
                thread_root_id=ROOT_ID,
            )
    assert exc.value.code == "INVALID_THREAD_ROOT"


@pytest.mark.asyncio
async def test_thread_root_must_be_a_root_message():
    """§97: `thread_root_id` references a root — threads never nest."""
    repo = MessagesRepository()
    nested = make_message(REPLY_ID, thread_root_id=ROOT_ID)

    with patch.object(MessageDocument, "get", new=AsyncMock(return_value=nested)):
        with pytest.raises(AppError) as exc:
            await repo._load_thread_root_in_container(
                container_type="conversation",
                container_id=CONVERSATION_ID,
                thread_root_id=REPLY_ID,
            )
    assert exc.value.code == "INVALID_THREAD_ROOT"


def test_wire_payloads_carry_the_container_envelope():
    """§77–78: the envelope rides along, `conversation_id` stays for clients."""
    post = make_message(ROOT_ID, container_type="channel", container_id=CHANNEL_ID)

    doc = to_message_doc(post)
    assert doc.container_type == "channel"
    assert doc.container_id == CHANNEL_ID
    assert doc.model_dump(mode="json")["conversation_id"] == CHANNEL_ID

    summary = to_thread_summary(post)
    assert (summary.container_type, summary.container_id) == ("channel", CHANNEL_ID)


# --- 6.2 authorization inherits from the container ---------------------------


class _AuthzHarness:
    """Runs `BaseMessagesService._require_container_permission` against a real
    `AuthorizationService` with a pre-seeded resource.

    ``membership`` is the caller's active membership of the container, or None
    for a caller with no standing there.
    """

    def __init__(
        self, resource_type: str, resource_id: str, resource, membership=None
    ) -> None:
        self.authorization = AuthorizationService()
        self.authorization.ownership._resources[(resource_type, resource_id)] = resource
        self.membership = membership
        self.service = BaseMessagesService(
            repo=MessagesRepository(), authorization=self.authorization
        )

    async def require(self, **kwargs) -> None:
        now = datetime.now(UTC)
        user = type(
            "U", (), {"id": PydanticObjectId(), "created_at": now, "updated_at": now}
        )()
        with (
            patch.object(
                AuthorizationService, "_load_user", new=AsyncMock(return_value=user)
            ),
            patch(
                "app.modules.authorization.service.RelationshipDocument.find_one",
                new=AsyncMock(return_value=self.membership),
            ),
            patch(
                "app.modules.authorization.service.BlockDocument.find_one",
                new=AsyncMock(return_value=None),
            ),
        ):
            await self.service._require_container_permission(**kwargs)


def make_channel(**overrides) -> ChannelDocument:
    fields = {
        "owner": OwnerRef(type="user", id=SENDER),
        "slug": "general",
        "name": "General",
        "created_by": SENDER,
    }
    fields.update(overrides)
    channel = ChannelDocument(**fields)
    channel.id = PydanticObjectId(CHANNEL_ID)
    return channel


@pytest.mark.asyncio
async def test_channel_posting_is_authorized_by_channel_policy():
    """The channel resource decides, not a message-local check (§91)."""
    harness = _AuthzHarness(
        "channel", CHANNEL_ID, make_channel(posting_policy="everyone")
    )
    await harness.require(
        user_id=OTHER_USER,
        action=MESSAGE_CREATE,
        container_type="channel",
        container_id=CHANNEL_ID,
    )


@pytest.mark.asyncio
async def test_channel_posting_denied_when_policy_narrows_it():
    harness = _AuthzHarness(
        "channel", CHANNEL_ID, make_channel(posting_policy="owner")
    )
    with pytest.raises(AppError) as exc:
        await harness.require(
            user_id=OTHER_USER,
            action=MESSAGE_CREATE,
            container_type="channel",
            container_id=CHANNEL_ID,
        )
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_own_message_actions_resolve_by_sender_id():
    """§59: an `*.own` action needs `sender_id == user_id`; the owner passes."""
    harness = _AuthzHarness("channel", CHANNEL_ID, make_channel())

    for action in (MESSAGE_EDIT_OWN, MESSAGE_DELETE_OWN):
        await harness.require(
            user_id=SENDER,
            action=action,
            container_type="channel",
            container_id=CHANNEL_ID,
            sender_id=SENDER,
        )


@pytest.mark.asyncio
async def test_own_message_actions_denied_for_a_non_author():
    harness = _AuthzHarness("channel", CHANNEL_ID, make_channel())

    with pytest.raises(AppError) as exc:
        await harness.require(
            user_id=OTHER_USER,
            action=MESSAGE_EDIT_OWN,
            container_type="channel",
            container_id=CHANNEL_ID,
            sender_id=SENDER,
        )
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_conversation_container_authorizes_its_own_messages():
    """The same call shape works for the other container type.

    A DM is ownerless, so the sender's standing comes from membership plus the
    `.own` authorship check — exactly the §91 inheritance being asserted.
    """
    conversation = ConversationDocument(
        type="dm", participant_ids=[SENDER, OTHER_USER], created_by=SENDER
    )
    conversation.id = PydanticObjectId(CONVERSATION_ID)
    harness = _AuthzHarness(
        "conversation",
        CONVERSATION_ID,
        conversation,
        membership=RelationshipDocument(
            kind="membership",
            user_id=SENDER,
            target_type="conversation",
            target_id=CONVERSATION_ID,
            status="active",
            initiation="direct",
            initiated_by=SENDER,
            permission_overrides=RelationshipPermissionOverrides(
                allow=[MESSAGE_DELETE_OWN], deny=[]
            ),
        ),
    )

    await harness.require(
        user_id=SENDER,
        action=MESSAGE_DELETE_OWN,
        container_type="conversation",
        container_id=CONVERSATION_ID,
        sender_id=SENDER,
    )
