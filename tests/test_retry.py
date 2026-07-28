from __future__ import annotations

import httpx
import pytest

from xiaoheihe.api_client import RateLimitedError, XiaoheiheApiClient
from xiaoheihe.models import Credentials
from xiaoheihe.rate_limiter import AsyncRateLimiter


def make_client(handler, max_retries=3):
    http_client = httpx.AsyncClient(
        base_url="https://api.xiaoheihe.cn",
        transport=httpx.MockTransport(handler),
    )
    client = XiaoheiheApiClient(
        "default",
        credentials=Credentials("default", "1", "bot", {"sid": "mock"}),
        client=http_client,
        min_request_interval_seconds=0,
        max_retries=max_retries,
    )
    client._limiter = AsyncRateLimiter(0, jitter_seconds=0)
    return client, http_client


async def test_429_retry_after_then_success() -> None:
    count = 0

    def handler(request):
        nonlocal count
        count += 1
        if count == 1:
            return httpx.Response(429, headers={"Retry-After": "0"})
        return httpx.Response(200, json={"result": {"uid": "1"}})

    client, http_client = make_client(handler)
    assert (await client.check_credentials())["uid"] == "1"
    assert count == 2
    await client.close()
    await http_client.aclose()


async def test_429_exhaustion_exposes_retry_after() -> None:
    def handler(request):
        return httpx.Response(429, headers={"Retry-After": "9"})

    client, http_client = make_client(handler, max_retries=0)
    with pytest.raises(RateLimitedError) as captured:
        await client.check_credentials()
    assert captured.value.retry_after == 9
    await client.close()
    await http_client.aclose()


async def test_5xx_retries_safe_get(monkeypatch) -> None:
    count = 0
    monkeypatch.setattr("xiaoheihe.api_client._backoff", lambda attempt: 0)

    def handler(request):
        nonlocal count
        count += 1
        if count < 3:
            return httpx.Response(503, json={"error": "busy"})
        return httpx.Response(200, json={"result": {"uid": "1"}})

    client, http_client = make_client(handler)
    await client.check_credentials()
    assert count == 3
    await client.close()
    await http_client.aclose()
