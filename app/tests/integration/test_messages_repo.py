from datetime import UTC, datetime

import pytest
from bson import ObjectId

from app.core.errors import AppError
from app.db.models import CallDocument, MediaDocument
from app.db.mongo import get_db
from app.modules.messages.repository import MessagesRepository
from app.modules.messages.repository.mappers import message_call, message_text


async def _add_participants(db, conversation_id: str, *user_ids: str) -> None:
    await db["conversation_participants"].delete_many(
        {"conversation_id": conversation_id}
    )
    now = datetime.now(UTC)
    await db["conversation_participants"].insert_many(
        [
            {
                "conversation_id": conversation_id,
                "user_id": user_id,
                "role": "member",
                "joined_at": now,
                "hidden": False,
                "muted": False,
                "created_at": now,
                "updated_at": now,
            }
            for user_id in user_ids
        ]
    )


@pytest.mark.asyncio
async def test_create_and_list_history_with_cursor():
    db = get_db()
    await db["messages"].delete_many({})

    repo = MessagesRepository()

    u1 = str(ObjectId())

    for i in range(3):
        await repo.create_conversation_message(
            conversation_id="conversation-history",
            sender_id=u1,
            message_type="media",
            media=MediaDocument(
                kind="voice",
                storage="local",
                key=f"uploads/{i}.m4a",
                mime="audio/m4a",
                size_bytes=123,
                duration_ms=1000,
            ),
        )

    items, next_cursor = await repo.list_history_for_conversation(
        conversation_id="conversation-history",
        user_id=u1,
        limit=2,
    )
    assert len(items) == 2
    assert next_cursor is not None

    items2, next2 = await repo.list_history_for_conversation(
        conversation_id="conversation-history",
        user_id=u1,
        limit=2,
        cursor=next_cursor,
    )
    assert len(items2) == 1
    assert next2 is None


@pytest.mark.asyncio
@pytest.mark.asyncio
async def test_message_receipts_track_delivered_and_read_counts():
    db = get_db()
    await db["messages"].delete_many({})
    await db["message_receipts"].delete_many({})

    repo = MessagesRepository()

    conversation_id = "conversation-receipts"
    sender_id = str(ObjectId())
    receiver_id = str(ObjectId())
    await _add_participants(db, conversation_id, sender_id, receiver_id)

    message = await repo.create_conversation_message(
        conversation_id=conversation_id,
        sender_id=sender_id,
        message_type="text",
        text="hello",
    )

    read_summary = await repo.upsert_message_receipt(
        conversation_id=conversation_id,
        message_id=message.str_id,
        user_id=receiver_id,
        delivered=True,
        read=True,
    )
    assert read_summary.recipient_count == 1
    assert read_summary.delivered_count == 1
    assert read_summary.read_count == 1

    delivered_summary = await repo.upsert_message_receipt(
        conversation_id=conversation_id,
        message_id=message.str_id,
        user_id=receiver_id,
        delivered=True,
    )
    assert delivered_summary.delivered_count == 1
    assert delivered_summary.read_count == 1


@pytest.mark.asyncio
async def test_hidden_message_is_excluded_only_for_hiding_user():
    db = get_db()
    await db["messages"].delete_many({})

    repo = MessagesRepository()

    sender_id = str(ObjectId())
    receiver_id = str(ObjectId())

    message = await repo.create_conversation_message(
        conversation_id="conversation-hidden",
        sender_id=sender_id,
        message_type="text",
        text="hide me",
    )

    await repo.hide_message_for_user(
        message_id=message.str_id,
        user_id=receiver_id,
    )

    receiver_items, _ = await repo.list_history_for_conversation(
        conversation_id="conversation-hidden",
        user_id=receiver_id,
        limit=20,
    )
    sender_items, _ = await repo.list_history_for_conversation(
        conversation_id="conversation-hidden",
        user_id=sender_id,
        limit=20,
    )

    assert receiver_items == []
    assert len(sender_items) == 1
    assert message_text(sender_items[0]) == "hide me"


@pytest.mark.asyncio
async def test_thread_replies_are_excluded_from_history_and_inherit_root():
    db = get_db()
    await db["messages"].delete_many({})

    repo = MessagesRepository()

    sender_id = str(ObjectId())
    receiver_id = str(ObjectId())

    conversation_id = "conversation-thread"
    root = await repo.create_conversation_message(
        conversation_id=conversation_id,
        sender_id=sender_id,
        message_type="text",
        text="root",
    )

    first_reply = await repo.create_conversation_thread_reply(
        conversation_id=conversation_id,
        sender_id=receiver_id,
        message_type="text",
        reply_to_message_id=root.str_id,
        text="first thread reply",
    )
    second_reply = await repo.create_conversation_thread_reply(
        conversation_id=conversation_id,
        sender_id=sender_id,
        message_type="text",
        reply_to_message_id=first_reply.str_id,
        text="second thread reply",
    )
    quote_reply = await repo.create_conversation_quote_reply(
        conversation_id=conversation_id,
        sender_id=sender_id,
        message_type="text",
        reply_to_message_id=root.str_id,
        text="quoted in timeline",
    )

    history, _ = await repo.list_history_for_conversation(
        conversation_id=conversation_id,
        user_id=sender_id,
        limit=20,
    )
    history_ids = [item.str_id for item in history]

    assert root.str_id in history_ids
    assert quote_reply.str_id in history_ids
    assert first_reply.str_id not in history_ids
    assert second_reply.str_id not in history_ids

    assert first_reply.thread_root_id == root.str_id
    assert second_reply.thread_root_id == root.str_id
    assert first_reply.reply_preview is not None
    assert second_reply.reply_preview is not None
    assert first_reply.reply_preview.message_id == root.str_id
    assert second_reply.reply_preview.message_id == first_reply.str_id

    thread_items = await repo.load_thread_messages_for_conversation(
        conversation_id=conversation_id,
        message_id=second_reply.str_id,
        user_id=receiver_id,
    )
    assert [message_text(item) for item in thread_items] == [
        "first thread reply",
        "second thread reply",
    ]

    summary = await repo.load_thread_summary_for_conversation(
        conversation_id=conversation_id,
        message_id=first_reply.str_id,
        user_id=sender_id,
    )
    assert summary.str_id == root.str_id
    assert summary.is_thread_root is True
    assert summary.thread_reply_count == 2
    assert summary.last_thread_reply_at is not None


@pytest.mark.asyncio
async def test_grouped_reactions_toggle_and_deleted_messages_reject_reactions():
    db = get_db()
    await db["messages"].delete_many({})

    repo = MessagesRepository()

    sender_id = str(ObjectId())
    receiver_id = str(ObjectId())

    message = await repo.create_conversation_message(
        conversation_id="conversation-reactions",
        sender_id=sender_id,
        message_type="text",
        text="react to me",
    )
    message_id = message.str_id

    first = await repo.add_or_toggle_grouped_reaction(
        message_id=message_id,
        user_id=sender_id,
        emoji="🔥",
    )
    assert len(first.reactions) == 1
    assert first.reactions[0].user_ids == [sender_id]
    assert first.reactions[0].count == 1

    second = await repo.add_or_toggle_grouped_reaction(
        message_id=message_id,
        user_id=receiver_id,
        emoji="🔥",
    )
    assert second.reactions[0].count == 2
    assert set(second.reactions[0].user_ids) == {sender_id, receiver_id}

    third = await repo.add_or_toggle_grouped_reaction(
        message_id=message_id,
        user_id=sender_id,
        emoji="🔥",
    )
    assert third.reactions[0].count == 1
    assert third.reactions[0].user_ids == [receiver_id]

    fourth = await repo.remove_grouped_reaction(
        message_id=message_id,
        user_id=receiver_id,
        emoji="🔥",
    )
    assert fourth.reactions == []

    await db["messages"].update_one(
        {"_id": message.id},
        {"$set": {"hidden_for_user_ids": [sender_id]}},
    )

    with pytest.raises(AppError) as exc:
        await repo.add_or_toggle_grouped_reaction(
            message_id=message_id,
            user_id=sender_id,
            emoji="🔥",
        )

    assert exc.value.code == "MESSAGE_NOT_REACTABLE"


@pytest.mark.asyncio
async def test_create_call_message_is_unique_per_call():
    db = get_db()
    await db["messages"].delete_many({})

    repo = MessagesRepository()
    call_oid = ObjectId()
    call_id = str(call_oid)
    started_at = datetime.now(UTC)
    caller_id = str(ObjectId())
    callee_id = str(ObjectId())

    call_doc = CallDocument.model_validate({
        "_id": call_oid,
        "caller_user_id": caller_id,
        "callee_user_id": callee_id,
        "participant_user_ids": [caller_id, callee_id],
        "type": "audio",
        "status": "ended",
        "room_id": f"call:{call_id}",
        "created_at": started_at,
        "updated_at": started_at,
        "answered_at": started_at,
        "ended_at": started_at,
        "expires_at": None,
        "reconnect_deadline_at": None,
        "disconnected_user_ids": [],
        "is_live": False,
    })

    first = await repo.create_call_message(
        call_doc=call_doc, conversation_id="conversation-call"
    )
    second = await repo.create_call_message(
        call_doc=call_doc, conversation_id="conversation-call"
    )

    assert first.str_id == second.str_id
    assert message_call(first) is not None
    assert message_call(first).call_id == call_id
    count = await db["messages"].count_documents(
        {"type": "call", "content.plaintext.call.call_id": call_id}
    )
    assert count == 1
