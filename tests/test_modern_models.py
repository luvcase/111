import json

import httpx
import pytest

from screenbridge.provider import AIProvider, ProviderError, Settings


@pytest.mark.parametrize("model", ["gpt-6-astra", "gpt-6.1-sol"])
async def test_modern_openai_model_parameters(monkeypatch, model):
    captured = []

    def handler(request):
        body = json.loads(request.content)
        captured.append(body)
        assert "max_tokens" not in body
        assert body["model"] == model
        assert body["max_completion_tokens"] >= 1024
        return httpx.Response(200, json={"choices": [{"message": {"content": "OK"}}]})

    original = httpx.AsyncClient
    monkeypatch.setattr(
        "screenbridge.provider.httpx.AsyncClient",
        lambda **kw: original(transport=httpx.MockTransport(handler), **kw),
    )
    provider = AIProvider(Settings(api_key="fixture", model=model, reasoning_effort="high"))
    assert (await provider.test_connection())["ok"]
    assert await provider.complete({"model": model, "max_tokens": 3500}) == "OK"
    assert captured[0]["max_completion_tokens"] == 1024
    assert "reasoning_effort" not in captured[0]
    assert captured[1]["max_completion_tokens"] == 16384
    assert captured[1]["reasoning_effort"] == "high"


async def test_compatible_provider_keeps_legacy_parameter(monkeypatch):
    def handler(request):
        body = json.loads(request.content)
        assert body["max_tokens"] == 16
        assert "max_completion_tokens" not in body
        assert "reasoning_effort" not in body
        return httpx.Response(200, json={"choices": [{"message": {"content": "OK"}}]})

    original = httpx.AsyncClient
    monkeypatch.setattr(
        "screenbridge.provider.httpx.AsyncClient",
        lambda **kw: original(transport=httpx.MockTransport(handler), **kw),
    )
    provider = AIProvider(Settings(api_key="fixture", base_url="https://api.example.com/v1"))
    assert (await provider.test_connection())["ok"]


async def test_truncated_answer_is_not_presented_as_complete(monkeypatch):
    def handler(request):
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": '{"answer":"half'}, "finish_reason": "length"}]
            },
        )

    original = httpx.AsyncClient
    monkeypatch.setattr(
        "screenbridge.provider.httpx.AsyncClient",
        lambda **kw: original(transport=httpx.MockTransport(handler), **kw),
    )
    provider = AIProvider(Settings(api_key="fixture", model="gpt-6-astra"))
    with pytest.raises(ProviderError, match="结果不完整"):
        await provider.analyze(None, "test", "explain")


async def test_reasoning_budget_error_is_actionable(monkeypatch):
    def handler(request):
        return httpx.Response(
            200, json={"choices": [{"message": {"content": ""}, "finish_reason": "length"}]}
        )

    original = httpx.AsyncClient
    monkeypatch.setattr(
        "screenbridge.provider.httpx.AsyncClient",
        lambda **kw: original(transport=httpx.MockTransport(handler), **kw),
    )
    provider = AIProvider(Settings(api_key="fixture", model="gpt-6-astra"))
    with pytest.raises(ProviderError, match="推理或输出达到本次预算"):
        await provider.test_connection()
