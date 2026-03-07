import uuid

import pytest

from app.modules.auth import service as auth_service


@pytest.mark.asyncio
async def test_register_verify_flow(inprocess_client, monkeypatch):
    email = f"sender-{uuid.uuid4().hex[:8]}@test.com"
    fixed_code = "123456"
    monkeypatch.setattr(auth_service, "_generate_6_digit_code", lambda: fixed_code)

    register_res = await inprocess_client.post("/auth/register", json={"email": email})
    assert register_res.status_code == 200

    verify_res = await inprocess_client.post(
        "/auth/verify",
        json={"email": email, "code": fixed_code},
    )
    assert verify_res.status_code == 200

    data = verify_res.json()["data"]
    assert "access_token" in data
    assert "refresh_token" in data

