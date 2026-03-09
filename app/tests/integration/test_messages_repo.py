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