from __future__ import annotations

from datetime import UTC, datetime, timedelta

from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from asgi_lifespan import LifespanManager
from bson import ObjectId
from httpx import ASGITransport, AsyncClient

from app.db.models import UserDocument
from app.db.mongo import get_db
from app.factory import create_app
from app.modules.auth.repository import UsersRepository
from app.modules.channels.repository import ChannelsRepository
from app.modules.channels.service import ChannelService
from app.modules.messages.dependencies import get_messages_service
from app.modules.notifications.dependencies import get_notifications_service
from app.tests.integration.test_realtime_socket import (
    _create_verified_user_and_tokens,
    _grant_chat_permission,
)
from app.workers.scheduled_worker import release_and_deliver


def _auth(access_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {access_token}"}


@pytest_asyncio.fixture(scope="function")
async def sio_spy_client():
    """A client whose socket server is a spy, so emits can be asserted directly.

    `get_sio` resolves `request.app.state.sio`, so swapping that attribute is enough
    to intercept every realtime emission the routes make.
    """
    app = create_app()
    sio = AsyncMock()
    app.state.sio = sio

    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport, base_url="http://testserver", timeout=10
        ) as client:
            yield client, sio


def _events(sio: AsyncMock) -> list[str]:
    return [call.args[0] for call in sio.emit.await_args_list if call.args]


def _rooms_for(sio: AsyncMock, event: str) -> set[str]:
    rooms = set()
    for call in sio.emit.await_args_list:
        if call.args and call.args[0] == event:
            room = call.kwargs.get("room")
            if room:
                rooms.add(room)
    return rooms


@pytest.mark.asyncio
async def test_channel_message_fans_out_to_followers(sio_spy_client):
    """Before this wiring, POST /channels/{id}/messages emitted nothing at all, so a
    channel open in the chat pane could never receive a message live."""
    client, sio = sio_spy_client

    owner, owner_tokens = await _create_verified_user_and_tokens("emit-owner@test.com")
    follower, follower_tokens = await _create_verified_user_and_tokens(
        "emit-follower@test.com"
    )

    created = await client.post(
        "/channels",
        json={
            "name": "Engineering",
            "kind": "text",
            "visibility": "public",
            "posting_policy": "everyone",
            "comment_policy": "everyone",
            "slug": "emit-engineering",
        },
        headers=_auth(owner_tokens["access_token"]),
    )
    assert created.status_code == 201, created.text
    channel_id = created.json()["data"]["id"]

    followed = await client.post(
        f"/channels/{channel_id}/follow",
        headers=_auth(follower_tokens["access_token"]),
    )
    assert followed.status_code == 201, followed.text

    sio.emit.reset_mock()

    sent = await client.post(
        f"/messages/channel/{channel_id}/text",
        json={"text": "shipping the rail today"},
        headers=_auth(owner_tokens["access_token"]),
    )
    assert sent.status_code == 201, sent.text

    assert "receive_message" in _events(sio), (
        f"expected a receive_message emit, got {_events(sio)}"
    )
    # Channels broadcast once to their room -- no per-follower emit, no ceiling.
    assert f"channel:{channel_id}" in _rooms_for(sio, "receive_message")


@pytest.mark.asyncio
async def test_channel_thread_reply_emits_thread_events(sio_spy_client):
    client, sio = sio_spy_client

    owner, owner_tokens = await _create_verified_user_and_tokens(
        "emit-thread-owner@test.com"
    )
    follower, follower_tokens = await _create_verified_user_and_tokens(
        "emit-thread-follower@test.com"
    )

    created = await client.post(
        "/channels",
        json={
            "name": "Threads",
            "kind": "text",
            "visibility": "public",
            "posting_policy": "everyone",
            "comment_policy": "everyone",
            "slug": "emit-threads",
        },
        headers=_auth(owner_tokens["access_token"]),
    )
    channel_id = created.json()["data"]["id"]

    await client.post(
        f"/channels/{channel_id}/follow",
        headers=_auth(follower_tokens["access_token"]),
    )

    root = await client.post(
        f"/messages/channel/{channel_id}/text",
        json={"text": "root post"},
        headers=_auth(owner_tokens["access_token"]),
    )
    root_id = root.json()["data"]["id"]

    sio.emit.reset_mock()

    reply = await client.post(
        f"/messages/channel/{channel_id}/text",
        json={
            "text": "a reply",
            "reply_mode": "thread",
            "reply_to_message_id": root_id,
        },
        headers=_auth(owner_tokens["access_token"]),
    )
    assert reply.status_code == 201, reply.text

    events = _events(sio)
    assert "thread_reply_created" in events, events
    assert "thread_summary_updated" in events, events


@pytest.mark.asyncio
async def test_profile_post_also_fans_out(sio_spy_client):
    """Profile posts are channel messages, so they travel the same fan-out path."""
    client, sio = sio_spy_client

    _owner, owner_tokens = await _create_verified_user_and_tokens(
        "emit-profile@test.com"
    )
    follower, follower_tokens = await _create_verified_user_and_tokens(
        "emit-profile-follower@test.com"
    )

    # Signup provisioning does not run through this test client, so provision the
    # profile channel the same way the other channel tests do.
    owner_doc = await UserDocument.get(_owner["_id"])
    assert owner_doc is not None
    profile_channel = await ChannelService(
        repo=ChannelsRepository()
    ).ensure_profile_channel(
        user_id=owner_doc.str_id,
        username=owner_doc.username,
    )
    await UsersRepository().update_main_channel(
        user_id=owner_doc.str_id,
        channel_id=profile_channel.str_id,
    )
    main_channel_id = profile_channel.str_id

    await client.post(
        f"/channels/{main_channel_id}/follow",
        headers=_auth(follower_tokens["access_token"]),
    )

    sio.emit.reset_mock()

    posted = await client.post(
        "/users/me/posts",
        json={"text": "hello from my profile"},
        headers=_auth(owner_tokens["access_token"]),
    )
    assert posted.status_code == 201, posted.text
    # The response must still be the message itself, not the internal send result.
    assert posted.json()["data"]["id"]

    assert f"channel:{main_channel_id}" in _rooms_for(sio, "receive_message")


@pytest.mark.asyncio
async def test_channel_read_reaches_the_reader_only(sio_spy_client):
    """A channel read is a personal event: the reader's own sessions, not the audience."""
    client, sio = sio_spy_client

    owner, owner_tokens = await _create_verified_user_and_tokens("read-owner@test.com")
    reader, reader_tokens = await _create_verified_user_and_tokens(
        "read-follower@test.com"
    )

    created = await client.post(
        "/channels",
        json={
            "name": "Announcements",
            "kind": "text",
            "visibility": "public",
            "posting_policy": "everyone",
            "comment_policy": "everyone",
            "slug": "read-announcements",
        },
        headers=_auth(owner_tokens["access_token"]),
    )
    assert created.status_code == 201, created.text
    channel_id = created.json()["data"]["id"]

    await client.post(
        f"/channels/{channel_id}/follow",
        headers=_auth(reader_tokens["access_token"]),
    )
    await client.post(
        f"/messages/channel/{channel_id}/text",
        json={"text": "please read"},
        headers=_auth(owner_tokens["access_token"]),
    )

    sio.emit.reset_mock()

    marked = await client.post(
        f"/channels/{channel_id}/read",
        headers=_auth(reader_tokens["access_token"]),
    )
    assert marked.status_code == 200, marked.text

    rooms = _rooms_for(sio, "channel_read")
    assert rooms == {f"user:{str(reader['_id'])}"}, (
        f"a channel read must reach only the reader's own sessions, reached {rooms}"
    )
    assert f"channel:{channel_id}" not in rooms


@pytest.mark.asyncio
async def test_channel_message_read_broadcasts_no_receipt_state(sio_spy_client):
    """Receipts need a recipient roster; a channel has followers, so the summary
    is structurally empty and must not be broadcast at all."""
    client, sio = sio_spy_client

    owner, owner_tokens = await _create_verified_user_and_tokens(
        "receipt-owner@test.com"
    )
    reader, reader_tokens = await _create_verified_user_and_tokens(
        "receipt-reader@test.com"
    )

    created = await client.post(
        "/channels",
        json={
            "name": "Receipts",
            "kind": "text",
            "visibility": "public",
            "posting_policy": "everyone",
            "comment_policy": "everyone",
            "slug": "receipt-channel",
        },
        headers=_auth(owner_tokens["access_token"]),
    )
    assert created.status_code == 201, created.text
    channel_id = created.json()["data"]["id"]

    posted = await client.post(
        f"/messages/channel/{channel_id}/text",
        json={"text": "no receipts here"},
        headers=_auth(owner_tokens["access_token"]),
    )
    assert posted.status_code == 201, posted.text
    message_id = posted.json()["data"]["id"]

    sio.emit.reset_mock()

    read = await client.post(
        f"/messages/{message_id}/read",
        headers=_auth(reader_tokens["access_token"]),
    )
    assert read.status_code == 200, read.text
    delivered = await client.post(
        f"/messages/{message_id}/delivered",
        headers=_auth(reader_tokens["access_token"]),
    )
    assert delivered.status_code == 200, delivered.text

    assert "message_status" not in _events(sio), (
        f"expected no receipt broadcast for a channel, got {_events(sio)}"
    )


@pytest.mark.asyncio
async def test_conversation_message_read_still_broadcasts_receipt_state(sio_spy_client):
    """The channel suppression must not have taken the conversation case with it."""
    client, sio = sio_spy_client

    sender, sender_tokens = await _create_verified_user_and_tokens(
        "receipt-dm-sender@test.com"
    )
    receiver, receiver_tokens = await _create_verified_user_and_tokens(
        "receipt-dm-receiver@test.com"
    )
    await _grant_chat_permission(str(sender["_id"]), str(receiver["_id"]))

    conversation = await client.post(
        "/conversations",
        json={"peer_user_id": str(receiver["_id"])},
        headers=_auth(sender_tokens["access_token"]),
    )
    # A DM is get-or-create, so this answers 200 rather than 201.
    assert conversation.status_code == 200, conversation.text
    conversation_id = conversation.json()["data"]["id"]

    posted = await client.post(
        f"/messages/conversation/{conversation_id}/text",
        json={"text": "receipts apply here"},
        headers=_auth(sender_tokens["access_token"]),
    )
    assert posted.status_code == 201, posted.text
    message_id = posted.json()["data"]["id"]

    sio.emit.reset_mock()

    read = await client.post(
        f"/messages/{message_id}/read",
        headers=_auth(receiver_tokens["access_token"]),
    )
    assert read.status_code == 200, read.text

    assert "message_status" in _events(sio), (
        f"expected a receipt broadcast for a conversation, got {_events(sio)}"
    )


@pytest.mark.asyncio
async def test_a_released_scheduled_channel_post_reaches_the_room(sio_spy_client):
    """The worker released scheduled channel posts with an empty recipient list,
    so nothing reached the channel's room and the channel never recorded them."""
    client, _sio = sio_spy_client
    _owner, owner_tokens = await _create_verified_user_and_tokens(
        "emit-scheduled-owner@test.com"
    )
    created = await client.post(
        "/channels",
        json={
            "name": "Scheduled",
            "kind": "text",
            "visibility": "public",
            "posting_policy": "everyone",
            "comment_policy": "everyone",
            "slug": "emit-scheduled",
        },
        headers=_auth(owner_tokens["access_token"]),
    )
    assert created.status_code == 201, created.text
    channel_id = created.json()["data"]["id"]

    scheduled = await client.post(
        f"/messages/channel/{channel_id}/schedule",
        json={
            "text": "goes out later",
            "scheduled_for": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
        },
        headers=_auth(owner_tokens["access_token"]),
    )
    assert scheduled.status_code == 201, scheduled.text
    scheduled_id = scheduled.json()["data"]["id"]
    await get_db()["messages"].update_one(
        {"_id": ObjectId(scheduled_id)},
        {"$set": {"scheduled_for": datetime.now(UTC) - timedelta(minutes=1)}},
    )

    worker_sio = AsyncMock()
    released = await release_and_deliver(
        sio=worker_sio,
        messages=get_messages_service(),
        notifications=get_notifications_service(),
    )
    assert released == 1
    assert f"channel:{channel_id}" in _rooms_for(worker_sio, "receive_message")

    channel = await ChannelsRepository().get_by_id(channel_id)
    assert channel is not None
    assert channel.last_message_id == scheduled_id
    assert channel.message_count == 1


@pytest.mark.asyncio
async def test_channel_pins_and_clear_for_everyone_reach_the_room(sio_spy_client):
    client, sio = sio_spy_client
    _owner, owner_tokens = await _create_verified_user_and_tokens(
        "emit-pins-owner@test.com"
    )
    created = await client.post(
        "/channels",
        json={
            "name": "Pins",
            "kind": "text",
            "visibility": "public",
            "posting_policy": "everyone",
            "comment_policy": "everyone",
            "slug": "emit-pins",
        },
        headers=_auth(owner_tokens["access_token"]),
    )
    assert created.status_code == 201, created.text
    channel_id = created.json()["data"]["id"]
    sent = await client.post(
        f"/messages/channel/{channel_id}/text",
        json={"text": "pin and clear"},
        headers=_auth(owner_tokens["access_token"]),
    )
    assert sent.status_code == 201, sent.text
    sio.emit.reset_mock()

    pinned = await client.post(
        f"/messages/{sent.json()['data']['id']}/pin",
        headers=_auth(owner_tokens["access_token"]),
    )
    assert pinned.status_code == 200, pinned.text
    assert f"channel:{channel_id}" in _rooms_for(sio, "conversation_pins_updated")

    cleared = await client.delete(
        f"/messages/channel/{channel_id}/all",
        headers=_auth(owner_tokens["access_token"]),
    )
    assert cleared.status_code == 200, cleared.text
    assert f"channel:{channel_id}" in _rooms_for(sio, "conversation_history_cleared")
