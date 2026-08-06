"""Built-in bots.

Top-level package (sibling of ``app/modules``) for first-party bots that ship with
the product. Currently: PollBot (see ``app/bots/poll``). Built-in bots are seeded
into the ``bots`` collection at startup by :func:`app.bots.seed.seed_builtin_bots`.
"""

from __future__ import annotations

from app.bots.registry import BUILTIN_BOTS, POLL_BOT, BuiltInBot

__all__ = ["BUILTIN_BOTS", "POLL_BOT", "BuiltInBot"]
