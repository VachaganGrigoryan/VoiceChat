from __future__ import annotations

import pytest
from pymongo import AsyncMongoClient

from app.core.config import settings
from app.db.init import DOCUMENT_MODELS

EXPECTED_COLLECTIONS = {
    "audit_logs",
    "blocks",
    "bots",
    "calls",
    "channels",
    "conversations",
    "device_prekeys",
    "devices",
    "discovery_tokens",
    "invite_links",
    "message_receipts",
    "messages",
    "notifications",
    "passkey_challenges",
    "passkeys",
    "polls",
    "push_tokens",
    "refresh_tokens",
    "relationships",
    "reports",
    "roles",
    "saved_messages",
    "slash_commands",
    "spaces",
    "users",
    "verification_codes",
    "webhooks",
}
LEGACY_COLLECTIONS = {
    "conversation_participants",
    "join_requests",
    "pings",
    "space_members",
}


@pytest.mark.asyncio
async def test_app_initializes_only_the_final_collection_set(
    app_lifecycle: None,
) -> None:
    registered = {model.get_settings().name for model in DOCUMENT_MODELS}
    assert registered == EXPECTED_COLLECTIONS
    assert registered.isdisjoint(LEGACY_COLLECTIONS)

    client: AsyncMongoClient = AsyncMongoClient(settings.mongo_uri)
    try:
        collections = set(
            await client[settings.mongo_db].list_collection_names()
        )
    finally:
        await client.close()

    assert collections == EXPECTED_COLLECTIONS
