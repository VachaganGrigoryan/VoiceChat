from __future__ import annotations

from collections.abc import Callable
from typing import Awaitable

from fastapi import Request

from app.core.rate_limit.limiter import RateLimitRule, rate_limiter


def get_client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()

    if request.client and request.client.host:
        return request.client.host

    return "unknown"


def rate_limit(
    rule: str,
    *,
    scope: str,
    key_func: Callable[[Request], str] | None = None,
) -> Callable[[Request], Awaitable[None]]:
    """
    FastAPI dependency factory.

    Works like Express middleware:
    dependencies=[Depends(rate_limit("5/minute", scope="auth_login"))]
    """

    rate_rule = RateLimitRule(rule)

    async def dependency(request: Request) -> None:
        identity = key_func(request) if key_func else get_client_ip(request)

        key = f"{scope}:{identity}"

        await rate_limiter.hit(
            key=key,
            rule=rate_rule,
        )

    return dependency


async def enforce_ip_rate_limit(
    request: Request,
    *,
    scope: str,
    rule: RateLimitRule,
) -> None:
    ip = get_client_ip(request)
    key = f"{scope}:ip:{ip}"
    await rate_limiter.hit(key=key, rule=rule)


async def enforce_ip_and_value_rate_limit(
    request: Request,
    *,
    scope: str,
    value: str,
    rule: RateLimitRule,
) -> None:
    ip = get_client_ip(request)
    normalized = value.strip().lower()
    key = f"{scope}:ip:{ip}:value:{normalized}"
    await rate_limiter.hit(key=key, rule=rule)


async def enforce_user_rate_limit(
    request: Request,
    *,
    scope: str,
    user_id: str,
    rule: RateLimitRule,
) -> None:
    ip = get_client_ip(request)
    key = f"{scope}:ip:{ip}:user:{user_id}"
    await rate_limiter.hit(key=key, rule=rule)