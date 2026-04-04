from __future__ import annotations

import uuid

import pytest

from app.core.security import decode_token
from app.db.mongo import get_db
from app.modules.auth import service as auth_service
from app.tests.integration.auth_helpers import finish_email_auth, start_email_auth


@pytest.mark.asyncio
async def test_start_finish_flow_for_new_email(inprocess_client, monkeypatch):
    email = f"sender-{uuid.uuid4().hex[:8]}@test.com"
    fixed_code = "123456"
    monkeypatch.setattr(auth_service, "_generate_6_digit_code", lambda: fixed_code)

    start_res = await start_email_auth(inprocess_client, email)
    assert start_res.status_code == 200

    start_body = start_res.json()
    assert start_body["success"] is True
    assert start_body["data"] == {
        "method": "email",
        "identifier": email.lower(),
        "message": "If the identifier can be used, a verification code has been sent.",
    }

    finish_res = await finish_email_auth(inprocess_client, email, fixed_code)
    assert finish_res.status_code == 200

    data = finish_res.json()["data"]
    assert "access_token" in data
    assert "refresh_token" in data
    assert data["token_type"] == "bearer"

    user = await get_db()["users"].find_one({"email": email.lower()})
    assert user is not None
    assert user["is_verified"] is True


@pytest.mark.asyncio
async def test_start_reuses_existing_verified_email_without_duplicate_user(inprocess_client, monkeypatch):
    email = f"verified-{uuid.uuid4().hex[:8]}@test.com"
    fixed_code = "123456"
    monkeypatch.setattr(auth_service, "_generate_6_digit_code", lambda: fixed_code)

    first_start = await start_email_auth(inprocess_client, email)
    assert first_start.status_code == 200
    first_finish = await finish_email_auth(inprocess_client, email, fixed_code)
    assert first_finish.status_code == 200

    db = get_db()
    original_user = await db["users"].find_one({"email": email.lower()})
    assert original_user is not None
    assert await db["users"].count_documents({"email": email.lower()}) == 1

    second_start = await start_email_auth(inprocess_client, email)
    assert second_start.status_code == 200
    assert await db["users"].count_documents({"email": email.lower()}) == 1

    second_finish = await finish_email_auth(inprocess_client, email, fixed_code)
    assert second_finish.status_code == 200

    token_payload = decode_token(second_finish.json()["data"]["access_token"])
    assert token_payload["sub"] == str(original_user["_id"])


@pytest.mark.asyncio
async def test_repeated_start_does_not_duplicate_unverified_user(inprocess_client, monkeypatch):
    email = f"repeat-{uuid.uuid4().hex[:8]}@test.com"
    fixed_code = "123456"
    monkeypatch.setattr(auth_service, "_generate_6_digit_code", lambda: fixed_code)

    first_start = await start_email_auth(inprocess_client, email)
    assert first_start.status_code == 200

    second_start = await start_email_auth(inprocess_client, email)
    assert second_start.status_code == 200

    db = get_db()
    assert await db["users"].count_documents({"email": email.lower()}) == 1

    finish_res = await finish_email_auth(inprocess_client, email, fixed_code)
    assert finish_res.status_code == 200

    user = await db["users"].find_one({"email": email.lower()})
    assert user is not None
    assert user["is_verified"] is True


@pytest.mark.asyncio
async def test_finish_returns_code_invalid_for_wrong_code(inprocess_client, monkeypatch):
    email = f"invalid-{uuid.uuid4().hex[:8]}@test.com"
    fixed_code = "123456"
    monkeypatch.setattr(auth_service, "_generate_6_digit_code", lambda: fixed_code)

    start_res = await start_email_auth(inprocess_client, email)
    assert start_res.status_code == 200

    finish_res = await finish_email_auth(inprocess_client, email, "000000")
    assert finish_res.status_code == 400

    body = finish_res.json()
    assert body["error"]["code"] == "CODE_INVALID"


@pytest.mark.asyncio
async def test_finish_rate_limits_attempts_and_invalidates_code(inprocess_client, monkeypatch):
    email = f"attempts-{uuid.uuid4().hex[:8]}@test.com"
    fixed_code = "123456"
    monkeypatch.setattr(auth_service, "_generate_6_digit_code", lambda: fixed_code)

    start_res = await start_email_auth(inprocess_client, email)
    assert start_res.status_code == 200

    for _ in range(5):
        bad_finish = await finish_email_auth(inprocess_client, email, "000000")
        assert bad_finish.status_code == 400
        assert bad_finish.json()["error"]["code"] == "CODE_INVALID"

    locked_finish = await finish_email_auth(inprocess_client, email, "000000")
    assert locked_finish.status_code == 429
    assert locked_finish.json()["error"]["code"] == "TOO_MANY_ATTEMPTS"

    expired_finish = await finish_email_auth(inprocess_client, email, fixed_code)
    assert expired_finish.status_code == 400
    assert expired_finish.json()["error"]["code"] == "CODE_INVALID"


@pytest.mark.asyncio
async def test_start_rejects_unsupported_method_with_validation_error(inprocess_client):
    response = await inprocess_client.post(
        "/auth/start",
        json={"method": "phone", "identifier": "+15555550123"},
    )

    assert response.status_code == 422
