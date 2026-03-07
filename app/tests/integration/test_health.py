import pytest


@pytest.mark.asyncio
async def test_health_live_is_public(inprocess_client):
    res = await inprocess_client.get("/health/live")
    assert res.status_code == 200
    assert res.json()["data"]["status"] == "up"


@pytest.mark.asyncio
async def test_health_ready_returns_status(inprocess_client):
    res = await inprocess_client.get("/health/ready")
    assert res.status_code in (200, 503)
    assert "status" in res.json()["data"]