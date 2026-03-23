from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

import httpx

from app.core.config import settings
from app.modules.calls.schemas import IceServer

_CF_GRAPHQL_URL = "https://api.cloudflare.com/client/v4/graphql"
_CF_TURN_CREDENTIALS_URL = (
    "https://rtc.live.cloudflare.com/v1/turn/keys/{key_id}/credentials/generate-ice-servers"
)
_BYTES_PER_GIB = 1024**3
_cloudflare_usage_cache: dict[str, "_CloudflareUsageSnapshot"] = {}
_cloudflare_usage_cache_lock = asyncio.Lock()


@dataclass(frozen=True)
class _CloudflareUsageSnapshot:
    checked_at: datetime
    egress_gb: float


class TurnProvider(ABC):
    name: str

    @abstractmethod
    async def get_ice_servers(self) -> list[IceServer]:
        raise NotImplementedError


class CoturnProvider(TurnProvider):
    name = "coturn"

    async def get_ice_servers(self) -> list[IceServer]:
        if not settings.coturn_urls or not settings.coturn_username or not settings.coturn_password:
            return []

        urls: str | list[str]
        urls = settings.coturn_urls[0] if len(settings.coturn_urls) == 1 else settings.coturn_urls
        return [
            IceServer(
                urls=urls,
                username=settings.coturn_username,
                credential=settings.coturn_password,
            )
        ]


class CloudflareProvider(TurnProvider):
    name = "cloudflare"

    async def get_ice_servers(self) -> list[IceServer]:
        if not settings.cf_turn_key_id or not settings.cf_turn_api_token:
            return []

        if await self._should_pause_for_usage():
            return []

        return await self._fetch_dynamic_ice_servers()

    async def _should_pause_for_usage(self) -> bool:
        if not settings.cf_account_id or settings.cf_turn_pause_at_gb <= 0:
            return False

        usage_gb = await self._get_usage_egress_gb()
        return usage_gb >= settings.cf_turn_pause_at_gb

    async def _fetch_dynamic_ice_servers(self) -> list[IceServer]:
        url = _CF_TURN_CREDENTIALS_URL.format(key_id=settings.cf_turn_key_id)

        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                url,
                headers={"Authorization": f"Bearer {settings.cf_turn_api_token}"},
                json={"ttl": settings.turn_ttl_seconds},
            )
            response.raise_for_status()

        payload = response.json()
        ice_servers = payload.get("iceServers", [])
        return [IceServer.model_validate(server) for server in ice_servers]

    async def _get_usage_egress_gb(self) -> float:
        now = datetime.now(UTC)
        cache_key = self._usage_cache_key()
        snapshot = _cloudflare_usage_cache.get(cache_key)
        if snapshot and (now - snapshot.checked_at).total_seconds() < settings.cf_turn_usage_cache_seconds:
            return snapshot.egress_gb

        async with _cloudflare_usage_cache_lock:
            cached_snapshot = _cloudflare_usage_cache.get(cache_key)
            refresh_now = datetime.now(UTC)
            if cached_snapshot and (
                refresh_now - cached_snapshot.checked_at
            ).total_seconds() < settings.cf_turn_usage_cache_seconds:
                return cached_snapshot.egress_gb

            usage_gb = await self._fetch_usage_egress_gb()
            _cloudflare_usage_cache[cache_key] = _CloudflareUsageSnapshot(
                checked_at=refresh_now,
                egress_gb=usage_gb,
            )
            return usage_gb

    async def _fetch_usage_egress_gb(self) -> float:
        current_date = datetime.now(UTC).date()
        window_days = max(settings.cf_turn_usage_lookback_days, 1)
        start_date = current_date - timedelta(days=window_days - 1)
        query = """
        query TurnUsage($accountId: String!, $keyId: String!, $dateFrom: Date!, $dateTo: Date!) {
          viewer {
            accounts(filter: { accountTag: $accountId }) {
              callsTurnUsageAdaptiveGroups(
                limit: 1
                filter: {
                  keyId: $keyId
                  date_geq: $dateFrom
                  date_leq: $dateTo
                }
                orderBy: [sum_egressBytes_DESC]
              ) {
                dimensions {
                  keyId
                }
                sum {
                  egressBytes
                }
              }
            }
          }
        }
        """

        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                _CF_GRAPHQL_URL,
                headers={"Authorization": f"Bearer {settings.cf_account_token}"},
                json={
                    "query": query,
                    "variables": {
                        "accountId": settings.cf_account_id,
                        "keyId": settings.cf_turn_key_id,
                        "dateFrom": _to_graphql_date(start_date),
                        "dateTo": _to_graphql_date(current_date),
                    },
                },
            )
            response.raise_for_status()

        payload = response.json()
        if payload.get("errors"):
            raise RuntimeError("Cloudflare TURN analytics query failed")

        accounts = payload.get("data", {}).get("viewer", {}).get("accounts", [])
        groups = accounts[0].get("callsTurnUsageAdaptiveGroups", []) if accounts else []
        egress_bytes = int(groups[0].get("sum", {}).get("egressBytes", 0)) if groups else 0
        print(f"Cloudflare TURN usage in the last {window_days} days: {egress_bytes} bytes, {self._egress_bytes_to_gb(egress_bytes):.2f} GB")
        return egress_bytes / _BYTES_PER_GIB

    def _usage_cache_key(self) -> str:
        return ":".join(
            [
                settings.cf_account_id,
                settings.cf_turn_key_id,
                str(settings.cf_turn_usage_lookback_days),
            ]
        )


def get_turn_providers() -> list[TurnProvider]:
    provider_order = [settings.turn_provider]
    if settings.turn_multi:
        for candidate in ("coturn", "cloudflare"):
            if candidate != settings.turn_provider:
                provider_order.append(candidate)

    providers: list[TurnProvider] = []
    seen: set[str] = set()

    for provider_name in provider_order:
        provider = _build_provider(provider_name)
        if provider is None or provider.name in seen:
            continue
        providers.append(provider)
        seen.add(provider.name)

    return providers


def _build_provider(provider_name: str) -> TurnProvider | None:
    if provider_name == "cloudflare":
        return CloudflareProvider()
    if provider_name == "coturn":
        return CoturnProvider()
    return None


def clear_turn_provider_cache() -> None:
    _cloudflare_usage_cache.clear()


def _to_graphql_date(value: date) -> str:
    return value.isoformat()
