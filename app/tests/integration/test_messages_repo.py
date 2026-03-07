import pytest
from bson import ObjectId

from app.db.mongo import get_db
from app.modules.messages.repository import MessagesRepository


@pytest.mark.asyncio
async def test_create_and_list_history():
    db = get_db()
    await db["messages"].delete_many({})

    repo = MessagesRepository(db)

    u1 = str(ObjectId())
    u2 = str(ObjectId())

    msg1 = await repo.create_voice_message(
        sender_id=u1,
        receiver_id=u2,
        audio={
            "storage": "local",
            "key": "uploads/a.m4a",
            "url": "http://localhost:8000/media/a.m4a",
            "mime": "audio/m4a",
            "size_bytes": 123,
            "duration_ms": 1000,
        },
    )
    assert msg1["_id"] is not None

    items, next_cursor = await repo.list_history(
        user_id=u1,
        peer_user_id=u2,
        limit=10,
    )
    assert len(items) == 1
    assert next_cursor is not None

    items2, next2 = await repo.list_history(
        user_id=u1,
        peer_user_id=u2,
        limit=10,
        cursor=next_cursor,
    )
    assert items2 == []
    assert next2 is None