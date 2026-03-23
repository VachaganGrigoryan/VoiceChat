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
COL_DISCOVERY_TOKENS = "discovery_tokens"
COL_STICKER_PACKS = "sticker_packs"
COL_STICKERS = "stickers"
COL_STICKER_UPLOAD_SESSIONS = "sticker_upload_sessions"


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

    await db[COL_MESSAGES].create_index(
        [("conversation_id", ASCENDING), ("thread_root_id", ASCENDING), ("created_at", DESCENDING)],
        name="ix_messages_conversation_threadRoot_createdAt_desc",
    )

    await db[COL_MESSAGES].create_index(
        [("thread_root_id", ASCENDING), ("created_at", ASCENDING)],
        name="ix_messages_threadRoot_createdAt_asc",
    )

    await db[COL_MESSAGES].create_index(
        [("reply_to_message_id", ASCENDING)],
        name="ix_messages_replyToMessageId",
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

    await db[COL_MESSAGES].create_index(
        [("conversation_id", ASCENDING), ("receiver_id", ASCENDING), ("status", ASCENDING), ("created_at", DESCENDING)],
        name="ix_messages_conversation_receiver_status_createdAt_desc",
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
    await db[COL_PINGS].create_index(
        [("to_user_id", 1), ("created_at", -1), ("_id", -1)],
        name="idx_pings_incoming_cursor",
    )

    await db[COL_PINGS].create_index(
        [("from_user_id", 1), ("created_at", -1), ("_id", -1)],
        name="idx_pings_outgoing_cursor",
    )
    await db[COL_PINGS].create_index(
        [("pair_id", ASCENDING), ("status", ASCENDING)],
        name="ix_pings_pair_status",
    )


    # COL_DISCOVERY_TOKENS
    await db[COL_DISCOVERY_TOKENS].create_index("user_id", name="idx_discovery_user_id")
    await db[COL_DISCOVERY_TOKENS].create_index("type", name="idx_discovery_type")
    await db[COL_DISCOVERY_TOKENS].create_index("expires_at", name="idx_discovery_expires_at")
    await db[COL_DISCOVERY_TOKENS].create_index("is_active", name="idx_discovery_is_active")

    await db[COL_DISCOVERY_TOKENS].create_index(
        [("user_id", 1), ("type", 1), ("is_active", 1)],
        partialFilterExpression={"type": "code", "is_active": True},
        name="uniq_active_code_per_user",
    )

    # STICKERS
    await db[COL_STICKER_PACKS].create_index(
        [("slug", ASCENDING)],
        unique=True,
        name="ux_sticker_packs_slug",
    )
    await db[COL_STICKER_PACKS].create_index(
        [("owner_user_id", ASCENDING)],
        name="ix_sticker_packs_owner_user_id",
    )
    await db[COL_STICKER_PACKS].create_index(
        [("status", ASCENDING), ("visibility", ASCENDING)],
        name="ix_sticker_packs_status_visibility",
    )

    await db[COL_STICKERS].create_index(
        [("pack_id", ASCENDING), ("slug", ASCENDING)],
        unique=True,
        name="ux_stickers_pack_id_slug",
    )
    await db[COL_STICKERS].create_index(
        [("pack_id", ASCENDING)],
        name="ix_stickers_pack_id",
    )
    await db[COL_STICKERS].create_index(
        [("emoji_aliases", ASCENDING)],
        name="ix_stickers_emoji_aliases",
    )

    await db[COL_STICKER_UPLOAD_SESSIONS].create_index(
        [("expires_at", ASCENDING)],
        expireAfterSeconds=0,
        name="ttl_sticker_upload_sessions_expires",
    )
