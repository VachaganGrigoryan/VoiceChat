from __future__ import annotations

import logging
from datetime import UTC, datetime

from pymongo.errors import DuplicateKeyError

from app.bots.registry import BUILTIN_BOTS, BuiltInBot
from app.db.models import BotDocument, UserDocument

logger = logging.getLogger(__name__)


async def _ensure_bot_user(bot: BuiltInBot) -> UserDocument:
    """Find (or create) the backing user account for a built-in bot.

    Keyed on the bot's reserved email so it is stable and idempotent across
    restarts; tolerant of a concurrent insert from another worker.
    """
    existing = await UserDocument.find_one(UserDocument.email == bot.email)
    if existing is not None:
        return existing

    now = datetime.now(UTC)
    doc = UserDocument(
        email=bot.email,
        username=bot.username,
        display_name=bot.name,
        is_verified=True,
        is_private=True,
        default_discovery_enabled=False,
        created_at=now,
        updated_at=now,
    )
    try:
        await doc.insert()
        return doc
    except DuplicateKeyError:
        found = await UserDocument.find_one(UserDocument.email == bot.email)
        if found is None:
            raise
        return found


async def _ensure_builtin_bot(bot: BuiltInBot) -> BotDocument:
    """Upsert a built-in ``BotDocument`` keyed on its unique ``slug``."""
    existing = await BotDocument.find_one(BotDocument.slug == bot.slug)
    if existing is not None:
        return existing

    user = await _ensure_bot_user(bot)
    doc = BotDocument(
        user_id=user.str_id,
        name=bot.name,
        description=bot.description,
        builtin=True,
        slug=bot.slug,
    )
    try:
        await doc.insert()
    except DuplicateKeyError:
        found = await BotDocument.find_one(BotDocument.slug == bot.slug)
        if found is None:
            raise
        return found
    logger.info("Seeded built-in bot '%s' (user_id=%s)", bot.slug, doc.user_id)
    return doc


async def seed_builtin_bots() -> None:
    """Idempotently ensure every built-in bot exists. Safe to call on each startup."""
    for bot in BUILTIN_BOTS:
        await _ensure_builtin_bot(bot)
