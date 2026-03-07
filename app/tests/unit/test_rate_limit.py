import pytest
from starlette.requests import Request

from app.core.errors import AppError
from app.core.rate_limit.dependencies import get_client_ip, rate_limit
from app.core.rate_limit.limiter import RateLimitRule, rate_limiter


def make_request(
    *,
    client_host: str = "127.0.0.1",
    forwarded_for: str | None = None,
) -> Request:
    headers = []
    if forwarded_for is not None:
        headers.append((b"x-forwarded-for", forwarded_for.encode()))

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/test",
        "headers": headers,
        "client": (client_host, 12345),
    }
    return Request(scope)


def test_get_client_ip_from_forwarded_header():
    request = make_request(forwarded_for="203.0.113.10, 10.0.0.1")
    assert get_client_ip(request) == "203.0.113.10"


def test_get_client_ip_from_request_client():
    request = make_request(client_host="192.168.1.50")
    assert get_client_ip(request) == "192.168.1.50"


@pytest.mark.asyncio
async def test_rate_limiter_allows_until_limit():
    rule = RateLimitRule("2/minute")
    key = "unit-test:allow"

    # First two hits should pass
    await rate_limiter.hit(key=key, rule=rule)
    await rate_limiter.hit(key=key, rule=rule)


@pytest.mark.asyncio
async def test_rate_limiter_blocks_after_limit():
    rule = RateLimitRule("2/minute")
    key = "unit-test:block"

    await rate_limiter.hit(key=key, rule=rule)
    await rate_limiter.hit(key=key, rule=rule)

    with pytest.raises(AppError) as exc:
        await rate_limiter.hit(key=key, rule=rule)

    assert exc.value.code == "RATE_LIMITED"
    assert exc.value.status_code == 429


@pytest.mark.asyncio
async def test_rate_limit_dependency_uses_ip_by_default():
    dependency = rate_limit("2/minute", scope="dep-default-ip")
    request = make_request(client_host="10.10.10.10")

    await dependency(request)
    await dependency(request)

    with pytest.raises(AppError) as exc:
        await dependency(request)

    assert exc.value.code == "RATE_LIMITED"
    assert exc.value.status_code == 429


@pytest.mark.asyncio
async def test_rate_limit_dependency_uses_custom_key_func():
    def custom_key(request: Request) -> str:
        return "custom-user-key"

    dependency = rate_limit(
        "1/minute",
        scope="dep-custom-key",
        key_func=custom_key,
    )

    request1 = make_request(client_host="1.1.1.1")
    request2 = make_request(client_host="2.2.2.2")

    await dependency(request1)

    # Should still hit same custom key, regardless of client IP
    with pytest.raises(AppError) as exc:
        await dependency(request2)

    assert exc.value.code == "RATE_LIMITED"
    assert exc.value.status_code == 429