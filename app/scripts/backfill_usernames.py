from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from motor.motor_asyncio import AsyncIOMotorClient

from app.core.config import settings
from app.db.indexes import COL_USERS
from app.modules.auth.username import generate_username_candidate, normalize_username


async def generate_unique_username(col) -> str:
    for _ in range(100):
        candidate = normalize_username(generate_username_candidate())
        existing = await col.find_one({"username": candidate}, {"_id": 1})
        if not existing:
            return candidate
    raise RuntimeError("failed to generate unique username after many attempts")


async def main() -> None:
    client = AsyncIOMotorClient(settings.mongo_uri)
    db = client[settings.mongo_db]
    col = db[COL_USERS]

    cursor = col.find(
        {
            "$or": [
                {"username": {"$exists": False}},
                {"username": None},
                {"username": ""},
            ]
        },
        projection={"_id": 1, "email": 1, "username": 1},
    )

    total = 0
    updated = 0

    async for user in cursor:
        total += 1

        for _ in range(20):
            username = await generate_unique_username(col)
            now = datetime.now(UTC)

            res = await col.update_one(
                {
                    "_id": user["_id"],
                    "$or": [
                        {"username": {"$exists": False}},
                        {"username": None},
                        {"username": ""},
                    ],
                },
                {
                    "$set": {
                        "username": username,
                        "display_name": None,
                        "bio": None,
                        "avatar_url": None,
                        "is_private": False,
                        "default_discovery_enabled": True,
                        "last_seen_at": None,
                        "username_updated_at": None,
                        "updated_at": now,
                    }
                },
            )

            if res.modified_count == 1:
                updated += 1
                print(f"updated {user['_id']} ({user.get('email')}) -> {username}")
                break
        else:
            raise RuntimeError(f"failed to update user {user['_id']}")

    print(f"scanned={total}, updated={updated}")
    client.close()


if __name__ == "__main__":
    asyncio.run(main())