from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from app.bots.registry import POLL_BOT
from app.db.models import BotDocument, PollDocument
from app.workers.scheduled_worker import close_due_polls
from app.tests.integration.test_realtime_socket import (
    _create_verified_user_and_tokens,
    _grant_chat_permission,
)


def _auth(access_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {access_token}"}


async def _group(inprocess_client, owner_email: str, *member_emails: str):
    owner, owner_tokens = await _create_verified_user_and_tokens(owner_email)
    members: list[tuple[dict, dict]] = []
    for email in member_emails:
        member, member_tokens = await _create_verified_user_and_tokens(email)
        await _grant_chat_permission(str(owner["_id"]), str(member["_id"]))
        members.append((member, member_tokens))

    resp = await inprocess_client.post(
        "/conversations/groups",
        json={
            "title": "Group",
            "participant_ids": [str(m["_id"]) for m, _ in members],
        },
        headers=_auth(owner_tokens["access_token"]),
    )
    assert resp.status_code == 201, resp.text
    return owner, owner_tokens, members, resp.json()["data"]["id"]


def _poll_body(conversation_id: str, **overrides) -> dict:
    body = {
        "conversation_id": conversation_id,
        "question": "Ship it?",
        "options": [{"id": "yes", "text": "Yes"}, {"id": "no", "text": "No"}],
        "allows_multiple": False,
        "results_visibility": "after_vote",
    }
    body.update(overrides)
    return body


async def _create_poll(inprocess_client, tokens, body):
    return await inprocess_client.post(
        "/polls", json=body, headers=_auth(tokens["access_token"])
    )


@pytest.mark.asyncio
async def test_pollbot_is_seeded(inprocess_client):
    bot = await BotDocument.find_one(BotDocument.slug == POLL_BOT.slug)
    assert bot is not None and bot.builtin is True


@pytest.mark.asyncio
async def test_create_poll_is_bot_authored_and_links_message(inprocess_client):
    owner, owner_tokens, _members, conv = await _group(
        inprocess_client, "poll-a-owner@test.com", "poll-a-m1@test.com"
    )
    resp = await _create_poll(inprocess_client, owner_tokens, _poll_body(conv))
    assert resp.status_code == 201, resp.text
    data = resp.json()["data"]

    # Bot authors the message; poll credits the human creator.
    assert data["poll"]["created_by"] == str(owner["_id"])
    assert data["message"]["sender_id"] != str(owner["_id"])
    # Message links to the poll (no embedded poll payload).
    assert data["message"]["type"] == "poll"
    ref = data["message"]["content"]["plaintext"]["poll_ref"]
    assert ref["poll_id"] == data["poll"]["id"]
    assert ref["question"] == "Ship it?"

    # Inbox preview derives from the poll question.
    inbox = await inprocess_client.get(
        "/conversations", headers=_auth(owner_tokens["access_token"])
    )
    convo = next(c for c in inbox.json()["data"] if c["id"] == conv)
    assert convo["last_message_preview"]["text"] == "Ship it?"


@pytest.mark.asyncio
async def test_group_member_cannot_create_poll_until_toggle(inprocess_client):
    _owner, owner_tokens, members, conv = await _group(
        inprocess_client, "poll-b-owner@test.com", "poll-b-m1@test.com"
    )
    _member, member_tokens = members[0]

    blocked = await _create_poll(inprocess_client, member_tokens, _poll_body(conv))
    assert blocked.status_code == 403
    assert blocked.json()["error"]["code"] == "POLL_CREATE_FORBIDDEN"

    # Owner enables member polls via the whitelisted settings endpoint.
    toggle = await inprocess_client.patch(
        f"/conversations/{conv}/settings",
        json={"allow_member_polls": True},
        headers=_auth(owner_tokens["access_token"]),
    )
    assert toggle.status_code == 200, toggle.text
    assert toggle.json()["data"]["settings"]["allow_member_polls"] is True

    allowed = await _create_poll(inprocess_client, member_tokens, _poll_body(conv))
    assert allowed.status_code == 201, allowed.text


@pytest.mark.asyncio
async def test_member_cannot_toggle_settings(inprocess_client):
    _owner, _owner_tokens, members, conv = await _group(
        inprocess_client, "poll-c-owner@test.com", "poll-c-m1@test.com"
    )
    _member, member_tokens = members[0]
    resp = await inprocess_client.patch(
        f"/conversations/{conv}/settings",
        json={"allow_member_polls": True},
        headers=_auth(member_tokens["access_token"]),
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "CONVERSATION_FORBIDDEN"


@pytest.mark.asyncio
async def test_single_choice_guard_and_one_vote_per_user(inprocess_client):
    _owner, owner_tokens, _members, conv = await _group(
        inprocess_client, "poll-d-owner@test.com", "poll-d-m1@test.com"
    )
    poll = (await _create_poll(inprocess_client, owner_tokens, _poll_body(conv))).json()[
        "data"
    ]["poll"]
    pid = poll["id"]

    def vote(option_ids):
        return inprocess_client.post(
            f"/polls/{pid}/vote",
            json={"option_ids": option_ids},
            headers=_auth(owner_tokens["access_token"]),
        )

    bad = await vote(["yes", "no"])
    assert bad.status_code == 400 and bad.json()["error"]["code"] == "POLL_SINGLE_CHOICE"

    first = await vote(["yes"])
    assert first.status_code == 200
    assert {o["id"]: o["vote_count"] for o in first.json()["data"]["options"]} == {
        "yes": 1,
        "no": 0,
    }

    changed = await vote(["no"])  # replaces, does not add
    d = changed.json()["data"]
    assert d["total_votes"] == 1
    assert {o["id"]: o["vote_count"] for o in d["options"]} == {"yes": 0, "no": 1}
    assert d["my_option_ids"] == ["no"]


@pytest.mark.asyncio
async def test_multi_choice_dedupe_and_always_visibility(inprocess_client):
    _owner, owner_tokens, members, conv = await _group(
        inprocess_client, "poll-e-owner@test.com", "poll-e-m1@test.com"
    )
    _member, member_tokens = members[0]
    body = _poll_body(
        conv,
        allows_multiple=True,
        results_visibility="always",
        options=[
            {"id": "a", "text": "A"},
            {"id": "b", "text": "B"},
            {"id": "c", "text": "C"},
        ],
    )
    pid = (await _create_poll(inprocess_client, owner_tokens, body)).json()["data"][
        "poll"
    ]["id"]

    voted = await inprocess_client.post(
        f"/polls/{pid}/vote",
        json={"option_ids": ["a", "a", "b"]},
        headers=_auth(member_tokens["access_token"]),
    )
    assert voted.json()["data"]["my_option_ids"] == ["a", "b"]

    # `always` -> visible to a non-voter.
    seen = await inprocess_client.get(
        f"/polls/{pid}", headers=_auth(owner_tokens["access_token"])
    )
    d = seen.json()["data"]
    assert d["results_visible"] is True
    assert {o["id"]: o["vote_count"] for o in d["options"]} == {"a": 1, "b": 1, "c": 0}


@pytest.mark.asyncio
async def test_after_vote_hidden_then_retract(inprocess_client):
    _owner, owner_tokens, _members, conv = await _group(
        inprocess_client, "poll-f-owner@test.com", "poll-f-m1@test.com"
    )
    pid = (await _create_poll(inprocess_client, owner_tokens, _poll_body(conv))).json()[
        "data"
    ]["poll"]["id"]

    # Before voting, after_vote hides counts.
    before = await inprocess_client.get(
        f"/polls/{pid}", headers=_auth(owner_tokens["access_token"])
    )
    assert before.json()["data"]["results_visible"] is False

    await inprocess_client.post(
        f"/polls/{pid}/vote",
        json={"option_ids": ["yes"]},
        headers=_auth(owner_tokens["access_token"]),
    )
    retract = await inprocess_client.post(
        f"/polls/{pid}/retract", headers=_auth(owner_tokens["access_token"])
    )
    d = retract.json()["data"]
    assert d["my_option_ids"] == []
    assert d["results_visible"] is False  # after_vote, no longer voted


@pytest.mark.asyncio
async def test_close_authorization_and_vote_after_close(inprocess_client):
    _owner, owner_tokens, members, conv = await _group(
        inprocess_client, "poll-g-owner@test.com", "poll-g-m1@test.com"
    )
    _member, member_tokens = members[0]
    pid = (await _create_poll(inprocess_client, owner_tokens, _poll_body(conv))).json()[
        "data"
    ]["poll"]["id"]

    forbidden = await inprocess_client.post(
        f"/polls/{pid}/close", headers=_auth(member_tokens["access_token"])
    )
    assert forbidden.status_code == 403
    assert forbidden.json()["error"]["code"] == "POLL_CLOSE_FORBIDDEN"

    closed = await inprocess_client.post(
        f"/polls/{pid}/close", headers=_auth(owner_tokens["access_token"])
    )
    assert closed.status_code == 200 and closed.json()["data"]["closed"] is True

    late = await inprocess_client.post(
        f"/polls/{pid}/vote",
        json={"option_ids": ["yes"]},
        headers=_auth(owner_tokens["access_token"]),
    )
    assert late.status_code == 409 and late.json()["error"]["code"] == "POLL_CLOSED"


@pytest.mark.asyncio
async def test_deadline_autoclose_sweep(inprocess_client):
    _owner, owner_tokens, _members, conv = await _group(
        inprocess_client, "poll-h-owner@test.com", "poll-h-m1@test.com"
    )
    pid = (await _create_poll(inprocess_client, owner_tokens, _poll_body(conv))).json()[
        "data"
    ]["poll"]["id"]

    # Force the deadline into the past and run the sweep.
    poll = await PollDocument.get(pid)
    assert poll is not None
    poll.closes_at = datetime.now(UTC) - timedelta(minutes=1)
    await poll.save()

    sio = AsyncMock()
    closed_count = await close_due_polls(sio=sio, now=datetime.now(UTC))
    assert closed_count == 1
    # Broadcast fanned out to both participants.
    assert sio.emit.await_count == 2
    assert {c.args[0] for c in sio.emit.await_args_list} == {"poll_updated"}

    got = await inprocess_client.get(
        f"/polls/{pid}", headers=_auth(owner_tokens["access_token"])
    )
    assert got.json()["data"]["closed"] is True

    late = await inprocess_client.post(
        f"/polls/{pid}/vote",
        json={"option_ids": ["yes"]},
        headers=_auth(owner_tokens["access_token"]),
    )
    assert late.status_code == 409
