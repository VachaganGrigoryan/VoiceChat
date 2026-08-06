from __future__ import annotations

from app.db.models import BotDocument


class BotsRepository:
    """Read access to bot accounts (built-in lookups by slug)."""

    async def get_by_slug(self, slug: str) -> BotDocument | None:
        return await BotDocument.find_one(BotDocument.slug == slug)
