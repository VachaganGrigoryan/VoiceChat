import pytest
from bson import ObjectId

from app.core.errors import AppError
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


@pytest.mark.asyncio
async def test_thread_replies_are_excluded_from_history_and_inherit_root():
    db = get_db()
    await db["messages"].delete_many({})

    repo = MessagesRepository(db)

    sender_id = str(ObjectId())
    receiver_id = str(ObjectId())

    root = await repo.create_message(
        sender_id=sender_id,
        receiver_id=receiver_id,
        message_type="text",
        text="root",
    )

    first_reply = await repo.create_thread_reply(
        sender_id=receiver_id,
        receiver_id=sender_id,
        message_type="text",
        reply_to_message_id=str(root["_id"]),
        text="first thread reply",
    )
    second_reply = await repo.create_thread_reply(
        sender_id=sender_id,
        receiver_id=receiver_id,
        message_type="text",
        reply_to_message_id=str(first_reply["_id"]),
        text="second thread reply",
    )
    quote_reply = await repo.create_quote_reply(
        sender_id=sender_id,
        receiver_id=receiver_id,
        message_type="text",
        reply_to_message_id=str(root["_id"]),
        text="quoted in timeline",
    )

    history, _ = await repo.list_history(
        user_id=sender_id,
        peer_user_id=receiver_id,
        limit=20,
    )
    history_ids = [str(item["_id"]) for item in history]

    assert str(root["_id"]) in history_ids
    assert str(quote_reply["_id"]) in history_ids
    assert str(first_reply["_id"]) not in history_ids
    assert str(second_reply["_id"]) not in history_ids

    assert first_reply["thread_root_id"] == str(root["_id"])
    assert second_reply["thread_root_id"] == str(root["_id"])
    assert first_reply["reply_preview"]["message_id"] == str(root["_id"])
    assert second_reply["reply_preview"]["message_id"] == str(first_reply["_id"])

    thread_items = await repo.load_thread_messages(
        message_id=str(second_reply["_id"]),
        user_id=receiver_id,
    )
    assert [item["text"] for item in thread_items] == [
        "first thread reply",
        "second thread reply",
    ]

    summary = await repo.load_thread_summary(
        message_id=str(first_reply["_id"]),
        user_id=sender_id,
    )
    assert str(summary["_id"]) == str(root["_id"])
    assert summary["is_thread_root"] is True
    assert summary["thread_reply_count"] == 2
    assert summary["last_thread_reply_at"] is not None


@pytest.mark.asyncio
async def test_grouped_reactions_toggle_and_deleted_messages_reject_reactions():
    db = get_db()
    await db["messages"].delete_many({})

    repo = MessagesRepository(db)

    sender_id = str(ObjectId())
    receiver_id = str(ObjectId())

    message = await repo.create_message(
        sender_id=sender_id,
        receiver_id=receiver_id,
        message_type="text",
        text="react to me",
    )
    message_id = str(message["_id"])

    first = await repo.add_or_toggle_grouped_reaction(
        message_id=message_id,
        user_id=sender_id,
        emoji="🔥",
    )
    assert len(first["reactions"]) == 1
    assert first["reactions"][0]["user_ids"] == [sender_id]
    assert first["reactions"][0]["count"] == 1

    second = await repo.add_or_toggle_grouped_reaction(
        message_id=message_id,
        user_id=receiver_id,
        emoji="🔥",
    )
    assert second["reactions"][0]["count"] == 2
    assert set(second["reactions"][0]["user_ids"]) == {sender_id, receiver_id}

    third = await repo.add_or_toggle_grouped_reaction(
        message_id=message_id,
        user_id=sender_id,
        emoji="🔥",
    )
    assert third["reactions"][0]["count"] == 1
    assert third["reactions"][0]["user_ids"] == [receiver_id]

    fourth = await repo.remove_grouped_reaction(
        message_id=message_id,
        user_id=receiver_id,
        emoji="🔥",
    )
    assert fourth["reactions"] == []

    await db["messages"].update_one(
        {"_id": message["_id"]},
        {"$set": {"hidden_for_user_ids": [sender_id]}},
    )

    with pytest.raises(AppError) as exc:
        await repo.add_or_toggle_grouped_reaction(
            message_id=message_id,
            user_id=sender_id,
            emoji="🔥",
        )

    assert exc.value.code == "MESSAGE_NOT_REACTABLE"
