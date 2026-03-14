from __future__ import annotations

import base64
import json
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from webauthn import (
    base64url_to_bytes,
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    PublicKeyCredentialHint,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)


def utcnow() -> datetime:
    return datetime.now(UTC)


def to_base64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def from_base64url(data: str) -> bytes:
    return base64url_to_bytes(data)


def generate_challenge_bytes() -> bytes:
    return secrets.token_bytes(32)


def parse_options_to_dict(options: Any) -> dict[str, Any]:
    return json.loads(options_to_json(options))


def build_registration_options(
    *,
    rp_id: str,
    rp_name: str,
    user_id: str,
    user_name: str,
    user_display_name: str | None,
    exclude_credential_ids: list[str],
    challenge: bytes,
    timeout_ms: int = 60_000,
) -> dict[str, Any]:
    options = generate_registration_options(
        rp_id=rp_id,
        rp_name=rp_name,
        user_id=user_id.encode("utf-8"),
        user_name=user_name,
        user_display_name=user_display_name or user_name,
        challenge=challenge,
        timeout=timeout_ms,
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.PREFERRED,
            user_verification=UserVerificationRequirement.PREFERRED,
        ),
        exclude_credentials=[
            PublicKeyCredentialDescriptor(id=from_base64url(credential_id))
            for credential_id in exclude_credential_ids
        ]
        or None,
        hints=[PublicKeyCredentialHint.CLIENT_DEVICE, PublicKeyCredentialHint.SECURITY_KEY],
    )
    return parse_options_to_dict(options)


def build_authentication_options(
    *,
    rp_id: str,
    allow_credential_ids: list[str] | None,
    challenge: bytes,
    timeout_ms: int = 60_000,
) -> dict[str, Any]:
    options = generate_authentication_options(
        rp_id=rp_id,
        challenge=challenge,
        timeout=timeout_ms,
        allow_credentials=[
            PublicKeyCredentialDescriptor(id=from_base64url(credential_id))
            for credential_id in (allow_credential_ids or [])
        ]
        or None,
        user_verification=UserVerificationRequirement.PREFERRED,
    )
    return parse_options_to_dict(options)


def verify_registration(
    *,
    credential: dict[str, Any],
    expected_challenge: str,
    expected_rp_id: str,
    expected_origin: str | list[str],
    require_user_verification: bool = False,
):
    return verify_registration_response(
        credential=credential,
        expected_challenge=from_base64url(expected_challenge),
        expected_rp_id=expected_rp_id,
        expected_origin=expected_origin,
        require_user_verification=require_user_verification,
    )


def verify_authentication(
    *,
    credential: dict[str, Any],
    expected_challenge: str,
    expected_rp_id: str,
    expected_origin: str | list[str],
    credential_public_key: str,
    credential_current_sign_count: int,
    require_user_verification: bool = False,
):
    return verify_authentication_response(
        credential=credential,
        expected_challenge=from_base64url(expected_challenge),
        expected_rp_id=expected_rp_id,
        expected_origin=expected_origin,
        credential_public_key=from_base64url(credential_public_key),
        credential_current_sign_count=credential_current_sign_count,
        require_user_verification=require_user_verification,
    )


def expires_at(seconds: int) -> datetime:
    return utcnow() + timedelta(seconds=seconds)


def normalize_webauthn_credential_id(credential: dict[str, Any]) -> str:
    credential_id = credential.get("id")
    if not isinstance(credential_id, str) or not credential_id:
        raise ValueError("credential.id is required")
    return credential_id


def extract_transports(credential: dict[str, Any]) -> list[str] | None:
    response = credential.get("response")
    if not isinstance(response, dict):
        return None
    transports = response.get("transports")
    if isinstance(transports, list) and all(isinstance(item, str) for item in transports):
        return transports
    return None
