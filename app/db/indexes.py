from __future__ import annotations

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ASCENDING, DESCENDING


COL_USERS = "users"
COL_VERIFICATION = "verification_codes"
COL_MESSAGES = "messages"
COL_REFRESH_TOKENS = "refresh_tokens"
COL_PASSKEYS = "passkeys"
COL_PASSKEY_CHALLENGES = "passkey_challenges"
COL_PINGS = "pings"


async def ensure_indexes(db: AsyncIOMotorDatabase) -> None:
    # USERS: unique email
    await db[COL_USERS].create_index([("email", ASCENDING)], unique=True, name="ux_users_email")
    await db[COL_USERS].create_index(
        [("username", ASCENDING)],
        unique=True,
        name="ux_users_username",
    )

    await db[COL_USERS].create_index(
        [("is_private", ASCENDING)],
        name="ix_users_is_private",
    )

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

    # Optional but useful for inbox-like queries later:
    await db[COL_MESSAGES].create_index(
        [("receiver_id", ASCENDING), ("created_at", DESCENDING)],
        name="ix_messages_receiver_createdAt_desc",
    )

    await db[COL_MESSAGES].create_index(
        [("sender_id", ASCENDING), ("created_at", DESCENDING)],
        name="ix_messages_sender_createdAt_desc",
    )

    # REFRESH_TOKENS: unique token_hash
    await db[COL_REFRESH_TOKENS].create_index(
        [("token_hash", ASCENDING)],
        unique=True,
        name="ux_refresh_tokens_token_hash",
    )

    await db[COL_REFRESH_TOKENS].create_index(
        [("user_id", ASCENDING), ("created_at", DESCENDING)],
        name="ix_refresh_tokens_user_createdAt_desc",
    )

    await db[COL_REFRESH_TOKENS].create_index(
        [("expires_at", ASCENDING)],
        expireAfterSeconds=0,
        name="ttl_refresh_tokens_expires",
    )

    # COL_PASSKEY and CHALLENGES
    await db[COL_PASSKEYS].create_index(
        [("credential_id", ASCENDING)],
        unique=True,
        name="uniq_passkeys_credential_id",
    )

    await db[COL_PASSKEYS].create_index(
        [("user_id", ASCENDING)],
        name="idx_passkeys_user_id",
    )

    await db[COL_PASSKEY_CHALLENGES].create_index(
        [("challenge", ASCENDING)],
        name="idx_passkey_challenges_challenge",
    )

    await db[COL_PASSKEY_CHALLENGES].create_index(
        [("expires_at", ASCENDING)],
        name="idx_passkey_challenges_expires_at",
        expireAfterSeconds=0,
    )

    await db[COL_PASSKEY_CHALLENGES].create_index(
        [("flow", ASCENDING), ("user_id", ASCENDING), ("email", ASCENDING)],
        name="idx_passkey_challenges_flow_user_email",
    )

    # COL_PINGS
    await db[COL_PINGS].create_index("pair_id", unique=True, name="uniq_pings_pair_id")
    await db[COL_PINGS].create_index("from_user_id", name="idx_pings_from_user_id")
    await db[COL_PINGS].create_index("to_user_id", name="idx_pings_to_user_id")
    await db[COL_PINGS].create_index("status", name="idx_pings_status")
    await db[COL_PINGS].create_index("updated_at", name="idx_pings_updated_at")