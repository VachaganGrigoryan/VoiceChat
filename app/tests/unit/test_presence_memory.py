import pytest

from app.modules.realtime.presence.memory import InMemoryPresenceBackend


@pytest.mark.asyncio
async def test_memory_presence_multiple_connections():
    backend = InMemoryPresenceBackend()

    assert await backend.add_connection("u1", "sid1") is True
    assert await backend.is_online("u1") is True
    assert await backend.get_state("u1") == "online"
    assert await backend.add_connection("u1", "sid2") is False
    assert await backend.get_connection_count("u1") == 2

    assert await backend.remove_connection("u1", "sid1") is False
    assert await backend.is_online("u1") is True

    assert await backend.remove_connection("u1", "sid2") is True
    assert await backend.is_online("u1") is False
    assert await backend.get_state("u1") == "offline"


@pytest.mark.asyncio
async def test_memory_presence_tracks_away_and_dnd_as_distinct_online_states():
    backend = InMemoryPresenceBackend()

    await backend.add_connection("u1", "sid1")

    assert await backend.set_state("u1", "away") == "away"
    assert await backend.get_state("u1") == "away"
    assert await backend.is_online("u1") is True

    assert await backend.set_state("u1", "dnd") == "dnd"
    assert await backend.get_state("u1") == "dnd"
    assert await backend.is_online("u1") is True

    await backend.remove_connection("u1", "sid1")
    assert await backend.get_state("u1") == "offline"
