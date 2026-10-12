import asyncio

import httpx
import pytest

from screenbridge.provider import (
    AIProvider,
    ProviderError,
    Settings,
    rate_error_kind,
    retry_delay,
)


def fake_client(monkeypatch, handler):
    original = httpx.AsyncClient
    monkeypatch.setattr(
        "screenbridge.provider.httpx.AsyncClient",
        lambda **kw: original(transport=httpx.MockTransport(handler), **kw),
    )


async def test_quota_is_not_retried_or_leaked(monkeypatch):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(
            429,
            json={
                "error": {
                    "code": "insufficient_quota",
                    "message": "private-fixture-key org-private limit billing",
                }
            },
            headers={"retry-after": "1"},
        )

    fake_client(monkeypatch, handler)
    provider = AIProvider(Settings(api_key="private-fixture-key"))
    with pytest.raises(ProviderError, match="API 额度不足") as error:
        await provider.test_connection()
    assert len(requests) == 1
    assert "private-fixture-key" not in str(error.value) and "org-private" not in str(error.value)


async def test_transient_rate_limit_retries_then_succeeds(monkeypatch):
    requests, waits = [], []

    async def wait(seconds):
        waits.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", wait)

    def handler(request):
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(
                429, json={"error": {"code": "rate_limit_exceeded"}}, headers={"retry-after": "2"}
            )
        return httpx.Response(200, json={"choices": [{"message": {"content": "OK"}}]})

    fake_client(monkeypatch, handler)
    assert (await AIProvider(Settings(api_key="fixture")).test_connection())["ok"]
    assert len(requests) == 2 and len(waits) == 1 and 2 <= waits[0] <= 2.3


async def test_exhausted_rate_limit_has_bounded_retries(monkeypatch):
    requests, waits = [], []

    async def wait(seconds):
        waits.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", wait)

    def handler(request):
        requests.append(request)
        return httpx.Response(429, json={"error": {"code": "rate_limit_exceeded"}})

    fake_client(monkeypatch, handler)
    with pytest.raises(ProviderError, match="已自动重试 2 次"):
        await AIProvider(Settings(api_key="fixture")).test_connection()
    assert len(requests) == 3 and len(waits) == 2


async def test_long_reset_does_not_retry_before_server_allows(monkeypatch):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(
            429, json={"error": {"code": "rate_limit_exceeded"}}, headers={"retry-after": "120"}
        )

    fake_client(monkeypatch, handler)
    with pytest.raises(ProviderError, match="至少等待 120 秒"):
        await AIProvider(Settings(api_key="fixture")).test_connection()
    assert len(requests) == 1


@pytest.mark.parametrize(
    "body, expected",
    [
        (
            {"error": {"code": "rate_limit_exceeded", "message": "Request too large for model"}},
            "oversized",
        ),
        ({"error": {"type": "insufficient_quota"}}, "quota"),
        ({"error": "invalid-body"}, "unknown"),
        ({}, "unknown"),
    ],
)
def test_safe_error_classification(body, expected):
    assert rate_error_kind(httpx.Response(429, json=body)) == expected


def test_reset_headers_and_invalid_numeric_values():
    assert retry_delay(httpx.Response(429, headers={"retry-after-ms": "1500"}), 2) == 1.5
    assert (
        retry_delay(httpx.Response(429, headers={"x-ratelimit-reset-tokens": "1m2.5s"}), 2) == 62.5
    )
    assert retry_delay(httpx.Response(429, headers={"retry-after": "NaN"}), 2) == 2


async def test_unknown_429_is_not_blindly_retried(monkeypatch):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(429, text="private fixture upstream body")

    fake_client(monkeypatch, handler)
    with pytest.raises(ProviderError, match="尚不能区分") as error:
        await AIProvider(Settings(api_key="fixture")).test_connection()
    assert len(requests) == 1 and "private fixture upstream body" not in str(error.value)
