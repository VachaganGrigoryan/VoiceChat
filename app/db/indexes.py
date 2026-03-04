from __future__ import annotations

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ASCENDING, DESCENDING


COL_USERS = "users"
COL_VERIFICATION = "verification_codes"
COL_MESSAGES = "messages"


async def ensure_indexes(db: AsyncIOMotorDatabase) -> None:
    # USERS: unique email
    await db[COL_USERS].create_index([("email", ASCENDING)], unique=True, name="ux_users_email")

    # VERIFICATION: TTL expiry + lookup indexes
    await db[COL_VERIFICATION].create_index([("expires_at", ASCENDING)], expireAfterSeconds=0, name="ttl_verification_expires")
    await db[COL_VERIFICATION].create_index([("email", ASCENDING), ("purpose", ASCENDING)], name="ix_verification_email_purpose")
    await db[COL_VERIFICATION].create_index([("user_id", ASCENDING), ("purpose", ASCENDING)], name="ix_verification_user_purpose")

    # MESSAGES:
    # Recommended approach: store conversation_id = f"{minId}_{maxId}"
    # Index for history pagination: (conversation_id, created_at desc)
    await db[COL_MESSAGES].create_index(
        [("conversation_id", ASCENDING), ("created_at", DESCENDING)],
        name="ix_messages_conversation_createdAt_desc",
    )

    # Helpful secondary indexes
    await db[COL_MESSAGES].create_index([("receiver_id", ASCENDING), ("created_at", DESCENDING)], name="ix_messages_receiver_createdAt_desc")