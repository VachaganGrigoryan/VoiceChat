from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from fastapi import HTTPException, status

from app.modules.passkeys.helpers import (
    build_authentication_options,
    build_registration_options,
    expires_at,
    extract_transports,
    generate_challenge_bytes,
    normalize_webauthn_credential_id,
    to_base64url,
    utcnow,
    verify_authentication,
    verify_registration,
)
from app.modules.passkeys.repository import PasskeyChallengesRepository, PasskeysRepository
from app.modules.passkeys.schemas import PasskeyResponse
from app.core.config import settings


class UsersRepositoryProto(Protocol):
    async def find_by_id(self, user_id: str) -> dict[str, Any] | None: ...
    async def find_by_email(self, email: str) -> dict[str, Any] | None: ...
    async def set_has_passkey(self, user_id: str, has_passkey: bool) -> None: ...
    async def set_passkey_login_enabled(self, *, user_id: str, value: bool) -> None: ...


class AuthServiceProto(Protocol):
    async def issue_token_pair_for_user(self, user: dict[str, Any]) -> dict[str, str]: ...


@dataclass(slots=True)
class PasskeySettings:
    rp_id: str
    rp_name: str
    origin: str | list[str]
    challenge_ttl_seconds: int = 300
    require_user_verification: bool = False


class PasskeyService:
    def __init__(
        self,
        *,
        passkeys_repo: PasskeysRepository,
        challenges_repo: PasskeyChallengesRepository,
        users_repo: UsersRepositoryProto,
        auth_service: AuthServiceProto,
    ) -> None:
        self.passkeys_repo = passkeys_repo
        self.challenges_repo = challenges_repo
        self.users_repo = users_repo
        self.auth_service = auth_service
        self.settings = PasskeySettings(
            rp_id=settings.passkey_rp_id,
            rp_name=settings.passkey_rp_name,
            origin=settings.passkey_origin,
            challenge_ttl_seconds=settings.passkey_challenge_ttl_seconds,
        )

    async def start_registration(self, *, user_id: str, nickname: str | None = None) -> dict[str, Any]:
        user = await self.users_repo.find_by_id(user_id)
        if not user:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

        existing = await self.passkeys_repo.list_by_user_id(user_id)
        challenge = generate_challenge_bytes()
        options = build_registration_options(
            rp_id=self.settings.rp_id,
            rp_name=self.settings.rp_name,
            user_id=str(user["_id"] if "_id" in user else user_id),
            user_name=str(user.get("email") or user.get("username") or user_id),
            user_display_name=user.get("display_name") or user.get("username") or user.get("email"),
            exclude_credential_ids=[item["credential_id"] for item in existing],
            challenge=challenge,
        )
        await self.challenges_repo.create_challenge(
            flow="register",
            challenge=options["challenge"],
            user_id=user_id,
            expires_at=expires_at(self.settings.challenge_ttl_seconds),
            now=utcnow(),
        )
        if nickname:
            options["nickname"] = nickname
        return options

    async def finish_registration(
        self,
        *,
        user_id: str,
        credential: dict[str, Any],
        nickname: str | None = None,
    ) -> PasskeyResponse:
        now = utcnow()
        challenge = self._extract_client_challenge(credential)
        challenge_doc = await self.challenges_repo.consume_active_challenge(
            flow="register",
            challenge=challenge,
            user_id=user_id,
            now=now,
        )
        if not challenge_doc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or expired challenge")

        verification = verify_registration(
            credential=credential,
            expected_challenge=challenge_doc["challenge"],
            expected_rp_id=self.settings.rp_id,
            expected_origin=self.settings.origin,
            require_user_verification=self.settings.require_user_verification,
        )
        credential_id = to_base64url(verification.credential_id)
        existing = await self.passkeys_repo.find_by_credential_id(credential_id)
        if existing:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Passkey already registered")

        doc = await self.passkeys_repo.create_passkey(
            {
                "user_id": user_id,
                "credential_id": credential_id,
                "public_key": to_base64url(verification.credential_public_key),
                "sign_count": verification.sign_count,
                "transports": extract_transports(credential),
                "device_type": getattr(verification.credential_device_type, "value", None),
                "backed_up": verification.credential_backed_up,
                "nickname": nickname,
                "aaguid": verification.aaguid,
                "last_used_at": None,
                "created_at": now,
                "updated_at": now,
            }
        )
        await self.users_repo.set_has_passkey(user_id=user_id, value=True)
        return self._to_passkey_response(doc)

    async def start_authentication(self, *, email: str | None) -> dict[str, Any]:
        now = utcnow()
        user = None
        allow_credentials: list[str] | None = None
        normalized_email: str | None = None
        if email:
            normalized_email = email.lower().strip()
            user = await self.users_repo.find_by_email(normalized_email)
            if not user or not user.get("is_verified", False):
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No passkeys available for this account")
            passkeys = await self.passkeys_repo.list_by_user_id(str(user.get("_id") or user["id"]))
            if not passkeys:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No passkeys available for this account")
            allow_credentials = [item["credential_id"] for item in passkeys]

        challenge = generate_challenge_bytes()
        options = build_authentication_options(
            rp_id=self.settings.rp_id,
            allow_credential_ids=allow_credentials,
            challenge=challenge,
        )
        await self.challenges_repo.create_challenge(
            flow="authenticate",
            challenge=options["challenge"],
            user_id=str(user.get("_id") or user["id"]) if user else None,
            email=normalized_email,
            expires_at=expires_at(self.settings.challenge_ttl_seconds),
            now=now,
        )
        return options

    async def finish_authentication(
        self,
        *,
        credential: dict[str, Any],
        email: str | None,
    ) -> dict[str, str]:
        now = utcnow()
        credential_id = normalize_webauthn_credential_id(credential)
        passkey = await self.passkeys_repo.find_by_credential_id(credential_id)
        if not passkey:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown passkey")

        user = await self.users_repo.find_by_id(passkey["user_id"])
        if not user or not user.get("is_verified", False):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Passkey login is not allowed")

        challenge = self._extract_client_challenge(credential)
        challenge_doc = await self.challenges_repo.consume_active_challenge(
            flow="authenticate",
            challenge=challenge,
            user_id=passkey["user_id"],
            email=email.lower().strip() if email else None,
            now=now,
        )
        if not challenge_doc and email is None:
            challenge_doc = await self.challenges_repo.consume_active_challenge(
                flow="authenticate",
                challenge=challenge,
                user_id=passkey["user_id"],
                now=now,
            )
        if not challenge_doc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or expired challenge")

        verified = verify_authentication(
            credential=credential,
            expected_challenge=challenge_doc["challenge"],
            expected_rp_id=self.settings.rp_id,
            expected_origin=self.settings.origin,
            credential_public_key=passkey["public_key"],
            credential_current_sign_count=passkey["sign_count"],
            require_user_verification=self.settings.require_user_verification,
        )
        await self.passkeys_repo.update_sign_count(
            credential_id=credential_id,
            sign_count=verified.new_sign_count,
            now=now,
        )
        return await self.auth_service.issue_token_pair_for_user(user_id=str(user["_id"]))

    async def list_passkeys(self, *, user_id: str) -> list[PasskeyResponse]:
        docs = await self.passkeys_repo.list_by_user_id(user_id)
        return [self._to_passkey_response(doc) for doc in docs]

    async def delete_passkey(self, *, user_id: str, credential_id: str) -> bool:
        deleted = await self.passkeys_repo.delete_by_credential_id(user_id, credential_id)
        if deleted:
            count = await self.passkeys_repo.count_by_user_id(user_id)
            await self.users_repo.set_has_passkey(user_id=user_id, value=count > 0)
        return deleted

    def _extract_client_challenge(self, credential: dict[str, Any]) -> str:
        response = credential.get("response")
        if not isinstance(response, dict):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="credential.response is required")
        client_data_json = response.get("clientDataJSON")
        if not isinstance(client_data_json, str):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="clientDataJSON is required")
        import json
        from webauthn import base64url_to_bytes

        client_data = json.loads(base64url_to_bytes(client_data_json))
        challenge = client_data.get("challenge")
        if not isinstance(challenge, str) or not challenge:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Challenge not found in clientDataJSON")
        return challenge

    def _to_passkey_response(self, doc: dict[str, Any]) -> PasskeyResponse:
        return PasskeyResponse(
            credential_id=doc["credential_id"],
            nickname=doc.get("nickname"),
            transports=doc.get("transports"),
            device_type=doc.get("device_type"),
            backed_up=doc.get("backed_up"),
            aaguid=doc.get("aaguid"),
            created_at=doc["created_at"],
            last_used_at=doc.get("last_used_at"),
        )
