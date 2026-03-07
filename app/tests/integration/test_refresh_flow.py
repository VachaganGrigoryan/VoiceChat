import pytest

from app.modules.auth import service as auth_service


@pytest.mark.skip(reason="Run Single")
async def test_refresh_token_rotation(inprocess_client, monkeypatch):
    email = "rotate@test.com"
    fixed_code = "123456"

    monkeypatch.setattr(auth_service, "_generate_6_digit_code", lambda: fixed_code)

    register_res = await inprocess_client.post("/auth/register", json={"email": email})
    assert register_res.status_code == 200

    verify_res = await inprocess_client.post(
        "/auth/verify",
        json={"email": email, "code": fixed_code},
    )
    assert verify_res.status_code == 200

    tokens = verify_res.json()["data"]
    refresh_1 = tokens["refresh_token"]

    refresh_res = await inprocess_client.post(
        "/auth/refresh",
        json={"refresh_token": refresh_1},
    )
    assert refresh_res.status_code == 200

    tokens2 = refresh_res.json()["data"]
    refresh_2 = tokens2["refresh_token"]

    assert refresh_2 != refresh_1
    assert "access_token" in tokens2
    assert tokens2["token_type"] == "bearer"

    reuse_res = await inprocess_client.post(
        "/auth/refresh",
        json={"refresh_token": refresh_1},
    )
    assert reuse_res.status_code == 401