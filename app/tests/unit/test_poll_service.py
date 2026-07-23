from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from app.bots.poll.service import PollService, poll_broadcast_payload
from app.core.errors import AppError
from app.db.models import PollDocument
from app.db.models.poll import PollOptionDocument, PollVoteDocument
from app.modules.realtime.emits import emit_poll_updated


def _svc() -> PollService:
    # The helpers under test are pure (no repo/conversation/message deps), so an
    # un-initialized instance is enough.
    return PollService.__new__(PollService)


def _poll(**overrides) -> PollDocument:
    data: dict = dict(
        conversation_id="c1",
        created_by="creator",
        bot_id="bot",
        question="Q?",
        options=[
            PollOptionDocument(id="a", text="A"),
            PollOptionDocument(id="b", text="B"),
            PollOptionDocument(id="c", text="C"),
        ],
        allows_multiple=False,
        anonymous=False,
        results_visibility="after_vote",
    )
    data.update(overrides)
    return PollDocument(**data)


def _vote(user_id: str, *option_ids: str) -> PollVoteDocument:
    return PollVoteDocument(
        user_id=user_id, option_ids=list(option_ids), voted_at=datetime.now(UTC)
    )


# ---- _validate_selection -----------------------------------------------------


def test_single_choice_requires_exactly_one_option():
    svc = _svc()
    poll = _poll(allows_multiple=False)
    assert svc._validate_selection(poll, ["a"]) == ["a"]
    for bad in ([], ["a", "b"]):
        with pytest.raises(AppError) as exc:
            svc._validate_selection(poll, bad)
        assert exc.value.code == "POLL_SINGLE_CHOICE"


def test_multiple_choice_dedupes_preserving_order():
    svc = _svc()
    poll = _poll(allows_multiple=True)
    assert svc._validate_selection(poll, ["b", "a", "b", "a"]) == ["b", "a"]


def test_unknown_option_rejected():
    svc = _svc()
    poll = _poll(allows_multiple=True)
    with pytest.raises(AppError) as exc:
        svc._validate_selection(poll, ["a", "zzz"])
    assert exc.value.code == "POLL_INVALID_OPTION"


# ---- _ensure_open ------------------------------------------------------------


def test_ensure_open_allows_open_poll():
    _svc()._ensure_open(_poll(closed=False, closes_at=None))


def test_ensure_open_rejects_closed_poll():
    with pytest.raises(AppError) as exc:
        _svc()._ensure_open(_poll(closed=True))
    assert exc.value.code == "POLL_CLOSED"


def test_ensure_open_rejects_past_deadline_naive_and_aware():
    svc = _svc()
    aware_past = datetime.now(UTC) - timedelta(minutes=1)
    naive_past = aware_past.replace(tzinfo=None)  # how Mongo returns it
    for closes_at in (aware_past, naive_past):
        with pytest.raises(AppError) as exc:
            svc._ensure_open(_poll(closes_at=closes_at))
        assert exc.value.code == "POLL_CLOSED"


def test_ensure_open_allows_future_deadline():
    _svc()._ensure_open(_poll(closes_at=datetime.now(UTC) + timedelta(hours=1)))


# ---- results visibility + tallies -------------------------------------------


def test_always_visible_to_non_voter():
    poll = _poll(results_visibility="always", votes=[_vote("x", "a"), _vote("y", "a")])
    view = _svc()._to_view(poll, viewer_id="stranger")
    assert view.results_visible is True
    assert {o.id: o.vote_count for o in view.options} == {"a": 2, "b": 0, "c": 0}
    assert view.total_votes == 2
    assert view.my_option_ids == []


def test_after_vote_hidden_before_vote_visible_after():
    poll = _poll(results_visibility="after_vote", votes=[_vote("other", "a")])
    hidden = _svc()._to_view(poll, viewer_id="me")
    assert hidden.results_visible is False
    assert hidden.total_votes is None
    assert all(o.vote_count is None for o in hidden.options)

    poll.votes.append(_vote("me", "b"))
    shown = _svc()._to_view(poll, viewer_id="me")
    assert shown.results_visible is True
    assert shown.my_option_ids == ["b"]
    assert {o.id: o.vote_count for o in shown.options} == {"a": 1, "b": 1, "c": 0}


def test_after_close_hidden_while_open_even_if_voted():
    poll = _poll(results_visibility="after_close", votes=[_vote("me", "a")])
    assert _svc()._to_view(poll, viewer_id="me").results_visible is False
    poll.closed = True
    assert _svc()._to_view(poll, viewer_id="me").results_visible is True


def test_anonymous_view_exposes_no_voter_identities():
    poll = _poll(
        anonymous=True,
        results_visibility="always",
        votes=[_vote("secret_voter", "a")],
    )
    view = _svc()._to_view(poll, viewer_id="secret_voter")
    dumped = view.model_dump()
    # Only aggregate counts + the caller's own selection are ever exposed.
    assert "secret_voter" not in str({k: v for k, v in dumped.items() if k != "my_option_ids"})
    assert view.my_option_ids == ["a"]
    assert {o.id: o.vote_count for o in view.options}["a"] == 1


# ---- broadcast payload -------------------------------------------------------


def test_broadcast_payload_hides_totals_until_globally_visible():
    open_after_vote = _poll(results_visibility="after_vote", votes=[_vote("x", "a")])
    assert poll_broadcast_payload(open_after_vote)["total_votes"] is None

    always = _poll(results_visibility="always", votes=[_vote("x", "a")])
    assert poll_broadcast_payload(always)["total_votes"] == 1

    closed = _poll(results_visibility="after_vote", closed=True, votes=[_vote("x", "a")])
    payload = poll_broadcast_payload(closed)
    assert payload["total_votes"] == 1 and payload["closed"] is True


# ---- emit fan-out ------------------------------------------------------------


@pytest.mark.asyncio
async def test_emit_poll_updated_fans_out_per_participant():
    sio = AsyncMock()
    await emit_poll_updated(
        sio,
        participant_ids=["u1", "u2", "u3"],
        payload={"poll_id": "p1", "closed": False},
    )
    assert sio.emit.await_count == 3
    rooms = sorted(call.kwargs["room"] for call in sio.emit.await_args_list)
    assert rooms == ["user:u1", "user:u2", "user:u3"]
    events = {call.args[0] for call in sio.emit.await_args_list}
    assert events == {"poll_updated"}
