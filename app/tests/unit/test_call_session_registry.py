from __future__ import annotations

import pytest

from app.modules.calls.session_registry import InMemoryCallSessionRegistry


@pytest.mark.asyncio
async def test_unbind_sid_only_reports_disconnect_when_last_socket_leaves():
    registry = InMemoryCallSessionRegistry()

    await registry.bind_socket(call_id="call-1", user_id="u1", sid="sid-a")
    await registry.bind_socket(call_id="call-1", user_id="u1", sid="sid-b")

    results = await registry.unbind_sid("sid-a")

    assert len(results) == 1
    assert results[0].call_id == "call-1"
    assert results[0].user_id == "u1"
    assert results[0].remaining_connection_count == 1
    assert await registry.get_connection_count(call_id="call-1", user_id="u1") == 1


@pytest.mark.asyncio
async def test_clear_call_removes_all_bindings_for_participants():
    registry = InMemoryCallSessionRegistry()

    await registry.bind_socket(call_id="call-1", user_id="u1", sid="sid-a")
    await registry.bind_socket(call_id="call-1", user_id="u2", sid="sid-b")

    await registry.clear_call(call_id="call-1", participant_user_ids=["u1", "u2"])

    assert await registry.get_connection_count(call_id="call-1", user_id="u1") == 0
    assert await registry.get_connection_count(call_id="call-1", user_id="u2") == 0
    assert await registry.unbind_sid("sid-a") == []
