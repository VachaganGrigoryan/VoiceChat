"""Calls service, split per concern.

A thin facade composes per-concern mixins (mirrors `messages/service/`).
`CallsService` and `CallTerminalResult` are re-exported so existing
`from app.modules.calls.service import CallsService` imports keep working.
"""

from __future__ import annotations

from app.modules.calls.service.base import BaseCallsService, CallTerminalResult
from app.modules.calls.service.lifecycle import LifecycleCallsMixin
from app.modules.calls.service.mapping import MappingCallsMixin
from app.modules.calls.service.queries import QueriesCallsMixin


class CallsService(
    LifecycleCallsMixin,
    QueriesCallsMixin,
    MappingCallsMixin,
    BaseCallsService,
):
    pass


__all__ = [
    "CallsService",
    "CallTerminalResult",
]
