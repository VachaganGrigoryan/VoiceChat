from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from motor.motor_asyncio import AsyncIOMotorClient

from app.core.config import settings
from app.db.indexes import COL_MESSAGES


def normalize_media(audio: dict) -> dict:
    return {
        "storage": audio.get("storage", "local"),
        "key": audio.get("key", ""),
        "url": audio.get("url", ""),
        "mime": audio.get("mime", "application/octet-stream"),
        "size_bytes": int(audio.get("size_bytes", 0) or 0),
        "duration_ms": int(audio.get("duration_ms", 0) or 0),
    }


async def main() -> None:
    client = AsyncIOMotorClient(settings.mongo_uri)
    db = client[settings.mongo_db]
    col = db[COL_MESSAGES]

    query = {
        "$or": [
            {"audio": {"$exists": True}},
            {"media": {"$exists": False}},
            {"edited_at": {"$exists": False}},
            {"text": {"$exists": False}},
        ]
    }

    total = 0
    updated = 0

    async for msg in col.find(query):
        total += 1
        now = datetime.now(UTC)

        set_data: dict = {"updated_at": now}
        unset_data: dict = {}

        # Keep using `type` as the canonical field for now.
        if "type" not in msg or not msg.get("type"):
            set_data["type"] = "voice"

        # Migrate old voice payload: audio -> media
        if msg.get("audio") is not None and msg.get("media") is None:
            set_data["media"] = normalize_media(msg["audio"])
            unset_data["audio"] = ""

        # Ensure text exists for generic message shape
        if "text" not in msg:
            set_data["text"] = None

        # Ensure edited_at exists
        if "edited_at" not in msg:
            set_data["edited_at"] = None

        # Ensure status exists
        if "status" not in msg or not msg.get("status"):
            set_data["status"] = "sent"

        # Ensure timestamps exist
        if "created_at" not in msg:
            set_data["created_at"] = now
        if "updated_at" not in msg:
            set_data["updated_at"] = now

        update_doc: dict = {}
        if set_data:
            update_doc["$set"] = set_data
        if unset_data:
            update_doc["$unset"] = unset_data

        if update_doc:
            res = await col.update_one({"_id": msg["_id"]}, update_doc)
            if res.modified_count == 1:
                updated += 1
                print(f"updated message {msg['_id']}")

    print(f"scanned={total}, updated={updated}")

    await cleanup_old_fields(col)

    client.close()


async def cleanup_old_fields(col):
    print("Cleaning up legacy fields...")

    res = await col.update_many(
        {"audio": {"$exists": True}},
        {"$unset": {"audio": ""}},
    )

    print(f"Removed audio field from {res.modified_count} documents")


if __name__ == "__main__":
    asyncio.run(main())