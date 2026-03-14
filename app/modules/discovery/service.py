from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from fastapi import HTTPException, status

from app.infra.storage import build_storage_url
from app.modules.discovery.schemas import (
    CreateInviteLinkResponse,
    DiscoveryUserSummary,
    RegenerateCodeResponse,
)
from app.modules.discovery.security import (
    generate_code_token,
    generate_link_token,
    hash_token,
    preview_token,
)
from app.modules.discovery.repository import DiscoveryTokensRepository


class UsersRepositoryProto(Protocol):
    async def find_by_id(self, user_id: str) -> dict[str, Any] | None: ...
    async def find_by_username_prefix(self, q: str, limit: int) -> list[dict[str, Any]]: ...


class PresenceServiceProto(Protocol):
    async def is_online(self, user_id: str) -> bool: ...


@dataclass(slots=True)
class DiscoveryConfig:
    invite_base_url: str
    code_ttl_seconds: int | None = None
    default_link_ttl_seconds: int = 60 * 60 * 24 * 7


def ensure_utc(dt: datetime | None) -> datetime | None:
    if dt and dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt


class DiscoveryService:
    def __init__(
        self,
        *,
        repo: DiscoveryTokensRepository,
        users_repo: UsersRepositoryProto,
        presence_service: PresenceServiceProto | None = None,
        config: DiscoveryConfig,
    ) -> None:
        self.repo = repo
        self.users_repo = users_repo
        self.presence_service = presence_service
        self.config = config

    async def regenerate_code(self, *, user_id: str) -> RegenerateCodeResponse:
        now = datetime.now(UTC)
        raw = generate_code_token()
        token_hash = hash_token(raw)
        expires_at = (
            now + timedelta(seconds=self.config.code_ttl_seconds)
            if self.config.code_ttl_seconds
            else None
        )

        await self.repo.deactivate_active_codes_for_user(user_id=user_id)

        doc = await self.repo.create_token(
            {
                "user_id": user_id,
                "type": "code",
                "token_hash": token_hash,
                "token_preview": preview_token(raw),
                "expires_at": expires_at,
                "used_at": None,
                "max_uses": None,
                "use_count": 0,
                "is_active": True,
                "created_at": now,
                "updated_at": now,
            }
        )

        return RegenerateCodeResponse(
            code=raw,
            token_preview=doc["token_preview"],
            expires_at=expires_at,
        )

    async def create_link(
        self,
        *,
        user_id: str,
        expires_in_seconds: int | None,
        max_uses: int | None,
    ) -> CreateInviteLinkResponse:
        now = datetime.now(UTC)
        raw = generate_link_token()
        token_hash = hash_token(raw)
        ttl = expires_in_seconds or self.config.default_link_ttl_seconds
        expires_at = now + timedelta(seconds=ttl)

        await self.repo.create_token(
            {
                "user_id": user_id,
                "type": "link",
                "token_hash": token_hash,
                "token_preview": preview_token(raw),
                "expires_at": expires_at,
                "used_at": None,
                "max_uses": max_uses,
                "use_count": 0,
                "is_active": True,
                "created_at": now,
                "updated_at": now,
            }
        )

        return CreateInviteLinkResponse(
            token=raw,
            url=f"{self.config.invite_base_url.rstrip('/')}/invite/{raw}",
            expires_at=expires_at,
            max_uses=max_uses,
        )

    async def resolve_code(self, *, code: str, requester_user_id: str | None = None) -> DiscoveryUserSummary:
        return await self._resolve_token(
            raw_token=code,
            token_type="code",
            discovered_via="code",
            requester_user_id=requester_user_id,
        )

    async def resolve_link(self, *, token: str, requester_user_id: str | None = None) -> DiscoveryUserSummary:
        return await self._resolve_token(
            raw_token=token,
            token_type="link",
            discovered_via="link",
            requester_user_id=requester_user_id,
        )

    async def search_users(self, *, q: str, limit: int = 20) -> list[DiscoveryUserSummary]:
        query = q.strip().lower()
        if not query:
            return []

        users = await self.users_repo.find_by_username_prefix(query, limit)

        result: list[DiscoveryUserSummary] = []
        for user in users:
            if user.get("is_private", False):
                continue
            if user.get("default_discovery_enabled", True) is False:
                continue

            result.append(
                await self._to_summary(user, discovered_via="username")
            )

        return result

    async def _resolve_token(
        self,
        *,
        raw_token: str,
        token_type: str,
        discovered_via: str,
        requester_user_id: str | None,
    ) -> DiscoveryUserSummary:
        now = datetime.now(UTC)
        token_hash = hash_token(raw_token)
        doc = await self.repo.find_active_by_hash(token_hash=token_hash, token_type=token_type)
        if not doc:
            raise HTTPException(status_code=404, detail="Invalid discovery token")

        expires_at = ensure_utc(doc.get("expires_at"))
        if expires_at and expires_at < now:
            raise HTTPException(status_code=400, detail="Discovery token expired")

        max_uses = doc.get("max_uses")
        use_count = int(doc.get("use_count", 0))
        if max_uses is not None and use_count >= max_uses:
            raise HTTPException(status_code=400, detail="Discovery token exhausted")

        await self.repo.increment_use(token_id=str(doc["_id"]), now=now)

        user = await self.users_repo.find_by_id(doc["user_id"])
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        return await self._to_summary(
            user,
            discovered_via=discovered_via,
            requester_user_id=requester_user_id,
        )

    async def _to_summary(
        self,
        user: dict[str, Any],
        *,
        discovered_via: str,
        requester_user_id: str | None = None,
    ) -> DiscoveryUserSummary:
        user_id = str(user["_id"])
        online = await self.presence_service.is_online(user_id) if self.presence_service else False
        avatar = user.get("avatar")
        if avatar is not None:
            avatar["url"] = build_storage_url(avatar["storage"], avatar["key"])

        return DiscoveryUserSummary(
            id=user_id,
            username=user.get("username", ""),
            display_name=user.get("display_name"),
            avatar=avatar,
            is_online=online,
            can_ping=requester_user_id != user_id,
            discovered_via=discovered_via,
        )