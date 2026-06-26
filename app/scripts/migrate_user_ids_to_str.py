"""Backfill: convert ObjectId user-reference fields to plain strings.

These fields are now typed `StrId` (string storage) on their Beanie documents:
  - messages.sender_id, messages.receiver_id
  - refresh_tokens.user_id

Existing rows persisted them as BSON ObjectId. The repositories now write and
query these fields as strings, so legacy ObjectId rows must be converted or they
will stop matching delivery/read/history/revoke queries.

Idempotent: only rows whose field is still an ObjectId are rewritten, so the
script is safe to re-run.

Run inside the app environment:
    python -m app.scripts.migrate_user_ids_to_str
"""

from __future__ import annotations

import asyncio

from pymongo import AsyncMongoClient

from app.core.config import settings
from app.db.collections import COL_MESSAGES, COL_REFRESH_TOKENS


async def _stringify_field(col, field: str) -> int:
    result = await col.update_many(
        {field: {"$type": "objectId"}},
        [{"$set": {field: {"$toString": f"${field}"}}}],
    )
    return int(result.modified_count)


async def main() -> None:
    client = AsyncMongoClient(settings.mongo_uri)
    db = client[settings.mongo_db]

    try:
        messages = db[COL_MESSAGES]
        refresh_tokens = db[COL_REFRESH_TOKENS]

        sender = await _stringify_field(messages, "sender_id")
        receiver = await _stringify_field(messages, "receiver_id")
        refresh_user = await _stringify_field(refresh_tokens, "user_id")

        print(
            "converted: "
            f"messages.sender_id={sender}, "
            f"messages.receiver_id={receiver}, "
            f"refresh_tokens.user_id={refresh_user}"
        )
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
