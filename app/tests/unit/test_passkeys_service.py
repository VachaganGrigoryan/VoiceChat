from __future__ import annotations

import base64
import json
from datetime import datetime, UTC
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.modules.passkeys.schemas import PasskeyResponse
from app.modules.passkeys.service import PasskeyService


def _b64url_json(data: dict) -> str:
    raw = json.dumps(data).encode()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


@pytest.fixture
def service():
    passkeys_repo = AsyncMock()
    challenges_repo = AsyncMock()
    users_repo = AsyncMock()
    auth_service = AsyncMock()

    svc = PasskeyService(
        passkeys_repo=passkeys_repo,
        challenges_repo=challenges_repo,
        users_repo=users_repo,
        auth_service=auth_service,
    )
    return svc, passkeys_repo, challenges_repo, users_repo, auth_service


@pytest.fixture
def fixed_now():
    return datetime(2026, 3, 14, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
def registration_credential():
    return {
        "id": "cred-id",
        "response": {
            "clientDataJSON": _b64url_json({"challenge": "reg-challenge"})
        },
    }


@pytest.fixture
def authentication_credential():
    return {
        "id": "cred-id",
        "response": {
            "clientDataJSON": _b64url_json({"challenge": "auth-challenge"})
        },
    }


@pytest.mark.asyncio
async def test_start_registration_user_not_found(service):
    svc, _, _, users_repo, _ = service
    users_repo.find_by_id.return_value = None

    with pytest.raises(HTTPException) as exc:
        await svc.start_registration(user_id="u1")

    assert exc.value.status_code == 404
    assert exc.value.detail == "User not found"


@pytest.mark.asyncio
async def test_start_registration_creates_challenge(service, monkeypatch, fixed_now):
    svc, passkeys_repo, challenges_repo, users_repo, _ = service

    users_repo.find_by_id.return_value = {
        "_id": "u1",
        "email": "user@example.com",
        "display_name": "User",
    }
    passkeys_repo.list_by_user_id.return_value = [{"credential_id": "old-cred"}]

    monkeypatch.setattr("app.modules.passkeys.service.generate_challenge_bytes", lambda: b"challenge-bytes")
    monkeypatch.setattr("app.modules.passkeys.service.expires_at", lambda ttl: fixed_now)
    monkeypatch.setattr("app.modules.passkeys.service.utcnow", lambda: fixed_now)
    monkeypatch.setattr(
        "app.modules.passkeys.service.build_registration_options",
        lambda **kwargs: {
            "challenge": "reg-challenge",
            "rp": {"id": kwargs["rp_id"], "name": kwargs["rp_name"]},
            "excludeCredentials": kwargs["exclude_credential_ids"],
        },
    )

    result = await svc.start_registration(user_id="u1", nickname="MacBook")

    assert result["challenge"] == "reg-challenge"
    assert result["nickname"] == "MacBook"
    challenges_repo.create_challenge.assert_awaited_once_with(
        flow="register",
        challenge="reg-challenge",
        user_id="u1",
        expires_at=fixed_now,
        now=fixed_now,
    )


@pytest.mark.asyncio
async def test_finish_registration_invalid_or_expired_challenge(
    service,
    registration_credential,
    monkeypatch,
    fixed_now,
):
    svc, _, challenges_repo, _, _ = service
    monkeypatch.setattr("app.modules.passkeys.service.utcnow", lambda: fixed_now)
    challenges_repo.consume_active_challenge.return_value = None

    with pytest.raises(HTTPException) as exc:
        await svc.finish_registration(
            user_id="u1",
            credential=registration_credential,
        )

    assert exc.value.status_code == 400
    assert exc.value.detail == "Invalid or expired challenge"


@pytest.mark.asyncio
async def test_finish_registration_duplicate_passkey(
    service,
    registration_credential,
    monkeypatch,
    fixed_now,
):
    svc, passkeys_repo, challenges_repo, _, _ = service

    monkeypatch.setattr("app.modules.passkeys.service.utcnow", lambda: fixed_now)
    monkeypatch.setattr(
        "app.modules.passkeys.service.verify_registration",
        lambda **kwargs: SimpleNamespace(
            credential_id=b"cred-bytes",
            credential_public_key=b"pub-key",
            sign_count=1,
            credential_device_type=SimpleNamespace(value="single_device"),
            credential_backed_up=False,
            aaguid="aaguid-1",
        ),
    )
    monkeypatch.setattr("app.modules.passkeys.service.to_base64url", lambda b: "cred-123" if b == b"cred-bytes" else "pub-456")
    monkeypatch.setattr("app.modules.passkeys.service.extract_transports", lambda credential: ["internal"])

    challenges_repo.consume_active_challenge.return_value = {
        "challenge": "reg-challenge",
    }
    passkeys_repo.find_by_credential_id.return_value = {"credential_id": "cred-123"}

    with pytest.raises(HTTPException) as exc:
        await svc.finish_registration(
            user_id="u1",
            credential=registration_credential,
        )

    assert exc.value.status_code == 409
    assert exc.value.detail == "Passkey already registered"


@pytest.mark.asyncio
async def test_finish_registration_success(
    service,
    registration_credential,
    monkeypatch,
    fixed_now,
):
    svc, passkeys_repo, challenges_repo, users_repo, _ = service

    monkeypatch.setattr("app.modules.passkeys.service.utcnow", lambda: fixed_now)
    monkeypatch.setattr(
        "app.modules.passkeys.service.verify_registration",
        lambda **kwargs: SimpleNamespace(
            credential_id=b"cred-bytes",
            credential_public_key=b"pub-key",
            sign_count=7,
            credential_device_type=SimpleNamespace(value="multi_device"),
            credential_backed_up=True,
            aaguid="aaguid-1",
        ),
    )
    monkeypatch.setattr(
        "app.modules.passkeys.service.to_base64url",
        lambda b: "cred-123" if b == b"cred-bytes" else "pub-456",
    )
    monkeypatch.setattr("app.modules.passkeys.service.extract_transports", lambda credential: ["hybrid", "internal"])

    challenges_repo.consume_active_challenge.return_value = {
        "challenge": "reg-challenge",
    }
    passkeys_repo.find_by_credential_id.return_value = None
    passkeys_repo.create_passkey.return_value = {
        "credential_id": "cred-123",
        "nickname": "MacBook",
        "transports": ["hybrid", "internal"],
        "device_type": "multi_device",
        "backed_up": True,
        "aaguid": "aaguid-1",
        "created_at": fixed_now,
        "last_used_at": None,
    }

    result = await svc.finish_registration(
        user_id="u1",
        credential=registration_credential,
        nickname="MacBook",
    )

    assert isinstance(result, PasskeyResponse)
    assert result.credential_id == "cred-123"
    assert result.nickname == "MacBook"
    users_repo.set_has_passkey.assert_awaited_once_with(user_id="u1", value=True)


@pytest.mark.asyncio
async def test_start_authentication_with_email_user_not_found(service):
    svc, _, _, users_repo, _ = service
    users_repo.find_by_email.return_value = None

    with pytest.raises(HTTPException) as exc:
        await svc.start_authentication(email="user@example.com")

    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_start_authentication_with_email_not_verified(service):
    svc, _, _, users_repo, _ = service
    users_repo.find_by_email.return_value = {
        "_id": "u1",
        "email": "user@example.com",
        "is_verified": False,
    }

    with pytest.raises(HTTPException) as exc:
        await svc.start_authentication(email="user@example.com")

    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_start_authentication_without_passkeys_fails(service):
    svc, passkeys_repo, _, users_repo, _ = service
    users_repo.find_by_email.return_value = {
        "_id": "u1",
        "email": "user@example.com",
        "is_verified": True,
    }
    passkeys_repo.list_by_user_id.return_value = []

    with pytest.raises(HTTPException) as exc:
        await svc.start_authentication(email="user@example.com")

    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_start_authentication_with_email_success(service, monkeypatch, fixed_now):
    svc, passkeys_repo, challenges_repo, users_repo, _ = service

    users_repo.find_by_email.return_value = {
        "_id": "u1",
        "email": "user@example.com",
        "is_verified": True,
    }
    passkeys_repo.list_by_user_id.return_value = [
        {"credential_id": "cred-1"},
        {"credential_id": "cred-2"},
    ]

    monkeypatch.setattr("app.modules.passkeys.service.utcnow", lambda: fixed_now)
    monkeypatch.setattr("app.modules.passkeys.service.expires_at", lambda ttl: fixed_now)
    monkeypatch.setattr("app.modules.passkeys.service.generate_challenge_bytes", lambda: b"auth-bytes")
    monkeypatch.setattr(
        "app.modules.passkeys.service.build_authentication_options",
        lambda **kwargs: {
            "challenge": "auth-challenge",
            "allowCredentials": kwargs["allow_credential_ids"],
        },
    )

    result = await svc.start_authentication(email=" User@Example.com ")

    assert result["challenge"] == "auth-challenge"
    assert result["allowCredentials"] == ["cred-1", "cred-2"]
    challenges_repo.create_challenge.assert_awaited_once_with(
        flow="authenticate",
        challenge="auth-challenge",
        user_id="u1",
        email="user@example.com",
        expires_at=fixed_now,
        now=fixed_now,
    )


@pytest.mark.asyncio
async def test_start_authentication_discoverable_flow_without_email(service, monkeypatch, fixed_now):
    svc, _, challenges_repo, _, _ = service

    monkeypatch.setattr("app.modules.passkeys.service.utcnow", lambda: fixed_now)
    monkeypatch.setattr("app.modules.passkeys.service.expires_at", lambda ttl: fixed_now)
    monkeypatch.setattr("app.modules.passkeys.service.generate_challenge_bytes", lambda: b"auth-bytes")
    monkeypatch.setattr(
        "app.modules.passkeys.service.build_authentication_options",
        lambda **kwargs: {
            "challenge": "auth-challenge",
            "allowCredentials": kwargs["allow_credential_ids"],
        },
    )

    result = await svc.start_authentication(email=None)

    assert result["challenge"] == "auth-challenge"
    assert result["allowCredentials"] is None
    challenges_repo.create_challenge.assert_awaited_once_with(
        flow="authenticate",
        challenge="auth-challenge",
        user_id=None,
        email=None,
        expires_at=fixed_now,
        now=fixed_now,
    )


@pytest.mark.asyncio
async def test_finish_authentication_unknown_passkey(
    service,
    authentication_credential,
    monkeypatch,
):
    svc, passkeys_repo, _, _, _ = service
    monkeypatch.setattr("app.modules.passkeys.service.normalize_webauthn_credential_id", lambda credential: "cred-123")
    passkeys_repo.find_by_credential_id.return_value = None

    with pytest.raises(HTTPException) as exc:
        await svc.finish_authentication(
            credential=authentication_credential,
            email="user@example.com",
        )

    assert exc.value.status_code == 404
    assert exc.value.detail == "Unknown passkey"


@pytest.mark.asyncio
async def test_finish_authentication_unverified_user_forbidden(
    service,
    authentication_credential,
    monkeypatch,
):
    svc, passkeys_repo, _, users_repo, _ = service
    monkeypatch.setattr("app.modules.passkeys.service.normalize_webauthn_credential_id", lambda credential: "cred-123")
    passkeys_repo.find_by_credential_id.return_value = {
        "credential_id": "cred-123",
        "user_id": "u1",
        "public_key": "pub",
        "sign_count": 3,
    }
    users_repo.find_by_id.return_value = {
        "_id": "u1",
        "is_verified": False,
    }

    with pytest.raises(HTTPException) as exc:
        await svc.finish_authentication(
            credential=authentication_credential,
            email="user@example.com",
        )

    assert exc.value.status_code == 403
    assert exc.value.detail == "Passkey login is not allowed"


@pytest.mark.asyncio
async def test_finish_authentication_invalid_challenge_with_email(
    service,
    authentication_credential,
    monkeypatch,
    fixed_now,
):
    svc, passkeys_repo, challenges_repo, users_repo, _ = service

    monkeypatch.setattr("app.modules.passkeys.service.utcnow", lambda: fixed_now)
    monkeypatch.setattr("app.modules.passkeys.service.normalize_webauthn_credential_id", lambda credential: "cred-123")

    passkeys_repo.find_by_credential_id.return_value = {
        "credential_id": "cred-123",
        "user_id": "u1",
        "public_key": "pub",
        "sign_count": 3,
    }
    users_repo.find_by_id.return_value = {
        "_id": "u1",
        "email": "user@example.com",
        "is_verified": True,
    }
    challenges_repo.consume_active_challenge.return_value = None

    with pytest.raises(HTTPException) as exc:
        await svc.finish_authentication(
            credential=authentication_credential,
            email="user@example.com",
        )

    assert exc.value.status_code == 400
    assert exc.value.detail == "Invalid or expired challenge"


@pytest.mark.asyncio
async def test_finish_authentication_without_email_uses_fallback_lookup(
    service,
    authentication_credential,
    monkeypatch,
    fixed_now,
):
    svc, passkeys_repo, challenges_repo, users_repo, auth_service = service

    monkeypatch.setattr("app.modules.passkeys.service.utcnow", lambda: fixed_now)
    monkeypatch.setattr("app.modules.passkeys.service.normalize_webauthn_credential_id", lambda credential: "cred-123")
    monkeypatch.setattr(
        "app.modules.passkeys.service.verify_authentication",
        lambda **kwargs: SimpleNamespace(new_sign_count=9),
    )

    passkeys_repo.find_by_credential_id.return_value = {
        "credential_id": "cred-123",
        "user_id": "u1",
        "public_key": "pub-key",
        "sign_count": 3,
    }
    users_repo.find_by_id.return_value = {
        "_id": "u1",
        "email": "user@example.com",
        "is_verified": True,
    }
    challenges_repo.consume_active_challenge.side_effect = [
        None,
        {"challenge": "auth-challenge"},
    ]
    auth_service.issue_token_pair_for_user.return_value = {
        "access_token": "jwt",
        "refresh_token": "refresh",
        "token_type": "bearer",
    }

    result = await svc.finish_authentication(
        credential=authentication_credential,
        email=None,
    )

    assert result["access_token"] == "jwt"
    assert challenges_repo.consume_active_challenge.await_count == 2
    passkeys_repo.update_sign_count.assert_awaited_once_with(
        credential_id="cred-123",
        sign_count=9,
        now=fixed_now,
    )
    auth_service.issue_token_pair_for_user.assert_awaited_once_with(user_id="u1")


@pytest.mark.asyncio
async def test_finish_authentication_with_email_success(
    service,
    authentication_credential,
    monkeypatch,
    fixed_now,
):
    svc, passkeys_repo, challenges_repo, users_repo, auth_service = service

    monkeypatch.setattr("app.modules.passkeys.service.utcnow", lambda: fixed_now)
    monkeypatch.setattr("app.modules.passkeys.service.normalize_webauthn_credential_id", lambda credential: "cred-123")
    monkeypatch.setattr(
        "app.modules.passkeys.service.verify_authentication",
        lambda **kwargs: SimpleNamespace(new_sign_count=11),
    )

    passkeys_repo.find_by_credential_id.return_value = {
        "credential_id": "cred-123",
        "user_id": "u1",
        "public_key": "pub-key",
        "sign_count": 5,
    }
    users_repo.find_by_id.return_value = {
        "_id": "u1",
        "email": "user@example.com",
        "is_verified": True,
    }
    challenges_repo.consume_active_challenge.return_value = {
        "challenge": "auth-challenge",
    }
    auth_service.issue_token_pair_for_user.return_value = {
        "access_token": "jwt",
        "refresh_token": "refresh",
        "token_type": "bearer",
    }

    result = await svc.finish_authentication(
        credential=authentication_credential,
        email=" User@Example.com ",
    )

    assert result["refresh_token"] == "refresh"
    challenges_repo.consume_active_challenge.assert_awaited_once_with(
        flow="authenticate",
        challenge="auth-challenge",
        user_id="u1",
        email="user@example.com",
        now=fixed_now,
    )


@pytest.mark.asyncio
async def test_list_passkeys_maps_response(service, fixed_now):
    svc, passkeys_repo, _, _, _ = service
    passkeys_repo.list_by_user_id.return_value = [
        {
            "credential_id": "cred-1",
            "nickname": "MacBook",
            "transports": ["internal"],
            "device_type": "multi_device",
            "backed_up": True,
            "aaguid": "a1",
            "created_at": fixed_now,
            "last_used_at": None,
        }
    ]

    result = await svc.list_passkeys(user_id="u1")

    assert len(result) == 1
    assert isinstance(result[0], PasskeyResponse)
    assert result[0].credential_id == "cred-1"


@pytest.mark.asyncio
async def test_delete_passkey_false_does_not_update_user(service):
    svc, passkeys_repo, _, users_repo, _ = service
    passkeys_repo.delete_by_credential_id.return_value = False

    result = await svc.delete_passkey(user_id="u1", credential_id="cred-1")

    assert result is False
    users_repo.set_has_passkey.assert_not_awaited()


@pytest.mark.asyncio
async def test_delete_passkey_true_updates_has_passkey(service):
    svc, passkeys_repo, _, users_repo, _ = service
    passkeys_repo.delete_by_credential_id.return_value = True
    passkeys_repo.count_by_user_id.return_value = 0

    result = await svc.delete_passkey(user_id="u1", credential_id="cred-1")

    assert result is True
    users_repo.set_has_passkey.assert_awaited_once_with(user_id="u1", value=False)


def test_extract_client_challenge_missing_response(service):
    svc, *_ = service

    with pytest.raises(HTTPException) as exc:
        svc._extract_client_challenge({"id": "x"})

    assert exc.value.status_code == 400
    assert exc.value.detail == "credential.response is required"


def test_extract_client_challenge_missing_client_data_json(service):
    svc, *_ = service

    with pytest.raises(HTTPException) as exc:
        svc._extract_client_challenge({"response": {}})

    assert exc.value.status_code == 400
    assert exc.value.detail == "clientDataJSON is required"


def test_extract_client_challenge_missing_challenge(service):
    svc, *_ = service
    credential = {
        "response": {
            "clientDataJSON": _b64url_json({"type": "webauthn.create"})
        }
    }

    with pytest.raises(HTTPException) as exc:
        svc._extract_client_challenge(credential)

    assert exc.value.status_code == 400
    assert exc.value.detail == "Challenge not found in clientDataJSON"


def test_extract_client_challenge_success(service):
    svc, *_ = service
    credential = {
        "response": {
            "clientDataJSON": _b64url_json({"challenge": "hello-challenge"})
        }
    }

    result = svc._extract_client_challenge(credential)

    assert result == "hello-challenge"


def test_to_passkey_response(service, fixed_now):
    svc, *_ = service
    doc = {
        "credential_id": "cred-1",
        "nickname": "MacBook",
        "transports": ["internal"],
        "device_type": "multi_device",
        "backed_up": True,
        "aaguid": "aaguid-1",
        "created_at": fixed_now,
        "last_used_at": None,
    }

    result = svc._to_passkey_response(doc)

    assert isinstance(result, PasskeyResponse)
    assert result.credential_id == "cred-1"
    assert result.nickname == "MacBook"