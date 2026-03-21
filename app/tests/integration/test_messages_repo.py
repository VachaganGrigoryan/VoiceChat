import pytest
from bson import ObjectId

from app.db.mongo import get_db
from app.modules.messages.repository import MessagesRepository


@pytest.mark.asyncio
async def test_create_and_list_history_with_cursor():
    db = get_db()
    await db["messages"].delete_many({})

    repo = MessagesRepository(db)

    u1 = str(ObjectId())
    u2 = str(ObjectId())

    for i in range(3):
        await repo.create_voice_message(
            sender_id=u1,
            receiver_id=u2,
            audio={
                "storage": "local",
                "key": f"uploads/{i}.m4a",
                "url": f"http://localhost:8000/media/{i}.m4a",
                "mime": "audio/m4a",
                "size_bytes": 123,
                "duration_ms": 1000,
            },
        )

    items, next_cursor = await repo.list_history(
        user_id=u1,
        peer_user_id=u2,
        limit=2,
    )
    assert len(items) == 2
    assert next_cursor is not None

    items2, next2 = await repo.list_history(
        user_id=u1,
        peer_user_id=u2,
        limit=2,
        cursor=next_cursor,
    )
    assert len(items2) == 1
    assert next2 is None


@pytest.mark.asyncio
async def test_mark_read_sets_delivered_at_and_prevents_status_downgrade():
    db = get_db()
    await db["messages"].delete_many({})

    repo = MessagesRepository(db)

    sender_id = str(ObjectId())
    receiver_id = str(ObjectId())

    message = await repo.create_message(
        sender_id=sender_id,
        receiver_id=receiver_id,
        message_type="text",
        text="hello",
    )

    read_doc = await repo.mark_read_for_receiver(
        message_id=str(message["_id"]),
        receiver_id=receiver_id,
    )
    assert read_doc["status"] == "read"
    assert read_doc["read_at"] is not None
    assert read_doc["delivered_at"] is not None

    delivered_doc = await repo.mark_delivered_for_receiver(
        message_id=str(message["_id"]),
        receiver_id=receiver_id,
    )
    assert delivered_doc["status"] == "read"
    assert delivered_doc["read_at"] == read_doc["read_at"]
    assert delivered_doc["delivered_at"] == read_doc["delivered_at"]


@pytest.mark.asyncio
async def test_hidden_message_is_excluded_only_for_hiding_user():
    db = get_db()
    await db["messages"].delete_many({})

    repo = MessagesRepository(db)

    sender_id = str(ObjectId())
    receiver_id = str(ObjectId())

    message = await repo.create_message(
        sender_id=sender_id,
        receiver_id=receiver_id,
        message_type="text",
        text="hide me",
    )

    await repo.hide_message_for_user(
        message_id=str(message["_id"]),
        user_id=receiver_id,
    )

    receiver_items, _ = await repo.list_history(
        user_id=receiver_id,
        peer_user_id=sender_id,
        limit=20,
    )
    sender_items, _ = await repo.list_history(
        user_id=sender_id,
        peer_user_id=receiver_id,
        limit=20,
    )

    assert receiver_items == []
    assert len(sender_items) == 1
    assert sender_items[0]["text"] == "hide me"
