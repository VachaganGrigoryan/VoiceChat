from __future__ import annotations

from collections.abc import Iterable

from app.core.errors import AppError
from app.modules.calls.schemas import CallStatus

LIVE_CALL_STATUSES = frozenset({"ringing", "accepted", "connecting", "active", "reconnecting"})
RECOVERABLE_CALL_STATUSES = frozenset({"accepted", "connecting", "active", "reconnecting"})
TERMINAL_CALL_STATUSES = frozenset({"rejected", "cancelled", "expired", "ended"})

ALLOWED_TRANSITIONS: dict[CallStatus, frozenset[CallStatus]] = {
    "ringing": frozenset({"accepted", "rejected", "cancelled", "expired"}),
    "accepted": frozenset({"connecting", "reconnecting", "ended"}),
    "connecting": frozenset({"active", "reconnecting", "ended"}),
    "active": frozenset({"reconnecting", "ended"}),
    "reconnecting": frozenset({"connecting", "ended"}),
    "rejected": frozenset(),
    "cancelled": frozenset(),
    "expired": frozenset(),
    "ended": frozenset(),
}


def is_live_status(status: CallStatus) -> bool:
    return status in LIVE_CALL_STATUSES


def is_recoverable_status(status: CallStatus) -> bool:
    return status in RECOVERABLE_CALL_STATUSES


def ensure_transition(current_status: CallStatus, next_status: CallStatus) -> None:
    if next_status not in ALLOWED_TRANSITIONS.get(current_status, frozenset()):
        raise AppError(
            code="INVALID_CALL_STATE",
            message=f"Cannot transition call from {current_status} to {next_status}",
            status_code=409,
        )


def ensure_status_in(
    status: CallStatus,
    *,
    allowed_statuses: Iterable[CallStatus],
    message: str | None = None,
) -> None:
    allowed = tuple(allowed_statuses)
    if status not in allowed:
        raise AppError(
            code="INVALID_CALL_STATE",
            message=message or f"Call must be in one of: {', '.join(allowed)}",
            status_code=409,
        )
