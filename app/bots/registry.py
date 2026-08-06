from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BuiltInBot:
    """Static definition of a first-party bot.

    Identifies the stable ``slug`` used to seed/find the bot, its display metadata,
    the chat command that invokes it, and the reserved identity for its backing
    user account.
    """

    slug: str
    name: str
    description: str
    command: str
    username: str
    email: str


POLL_BOT = BuiltInBot(
    slug="poll",
    name="PollBot",
    description="Create and run polls in any chat.",
    command="/poll",
    username="pollbot",
    email="pollbot@bots.vogi.local",
)

# Single source of truth for the built-in bots seeded at startup.
BUILTIN_BOTS: tuple[BuiltInBot, ...] = (POLL_BOT,)
