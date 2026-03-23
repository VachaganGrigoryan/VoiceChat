from __future__ import annotations

import pytest

from app.core.errors import AppError
from app.modules.calls.state import ensure_transition, is_live_status


def test_ensure_transition_allows_valid_state_change():
    ensure_transition("ringing", "accepted")
    ensure_transition("accepted", "connecting")
    ensure_transition("connecting", "active")
    ensure_transition("active", "reconnecting")
    ensure_transition("reconnecting", "connecting")
    ensure_transition("active", "ended")
    ensure_transition("reconnecting", "ended")


def test_ensure_transition_rejects_invalid_state_change():
    with pytest.raises(AppError) as exc:
        ensure_transition("ringing", "active")

    assert exc.value.code == "INVALID_CALL_STATE"


def test_is_live_status_matches_expected_statuses():
    assert is_live_status("ringing") is True
    assert is_live_status("accepted") is True
    assert is_live_status("connecting") is True
    assert is_live_status("active") is True
    assert is_live_status("reconnecting") is True
    assert is_live_status("rejected") is False
    assert is_live_status("expired") is False
