import time

import httpx
import pytest
from fastapi.testclient import TestClient

from screenbridge.app import create_app
from screenbridge.provider import AIProvider, ProviderError, Settings


@pytest.mark.parametrize(
    "exception,phase",
    [
        (httpx.ReadTimeout, "等待接口回复"),
        (httpx.ConnectTimeout, "建立连接"),
        (httpx.WriteTimeout, "上传请求"),
        (httpx.PoolTimeout, "等待本机连接资源"),
    ],
)
async def test_timeout_phase_settings_and_no_retry(monkeypatch, exception, phase):
    calls = []

    def handler(request):
        calls.append(request)
        assert request.extensions["timeout"] == {
            "connect": 15,
            "read": 270,
            "write": 30,
            "pool": 15,
        }
        raise exception("private-fixture must not be reflected", request=request)

    original = httpx.AsyncClient
    monkeypatch.setattr(
        "screenbridge.provider.httpx.AsyncClient",
        lambda **kw: original(transport=httpx.MockTransport(handler), **kw),
    )
    provider = AIProvider(Settings(api_key="private-fixture", request_timeout_seconds=270))
    with pytest.raises(ProviderError, match=phase) as error:
        await provider.analyze(None, "test", "concise")
    assert len(calls) == 1
    assert error.value.diagnostics["timeout_phase"] == phase
    assert error.value.diagnostics["read_timeout_seconds"] == 270
    assert error.value.diagnostics["attempts"] == 1
    assert error.value.diagnostics["api_ms"] >= 0
    assert "private-fixture" not in str(error.value)


def test_qwen_timeout_diagnostics_survive_failed_job(monkeypatch):
    def handler(request):
        assert request.extensions["timeout"]["read"] == 180
        raise httpx.ReadTimeout("fixture", request=request)

    original = httpx.AsyncClient
    monkeypatch.setattr(
        "screenbridge.provider.httpx.AsyncClient",
        lambda **kw: original(transport=httpx.MockTransport(handler), **kw),
    )
    token = "timeout-test-device-token-12345"
    headers = {"Authorization": "Bearer " + token}
    settings = Settings(
        api_key="fixture",
        model="qwen3.8-omni-flash",
        base_url="https://ws-fixture.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
    )
    with TestClient(create_app(settings, token)) as client:
        response = client.post("/api/analyze", headers=headers, json={"text": "test"})
        assert response.status_code == 202
        for _ in range(100):
            job = client.get("/api/jobs", headers=headers).json()["jobs"][0]
            if job["status"] == "error":
                break
            time.sleep(0.01)
        result = job["results"][0]
        assert job["status"] == "error"
        assert result["diagnostics"]["timeout_phase"] == "等待接口回复"
        assert result["diagnostics"]["read_timeout_seconds"] == 180
        assert result["elapsed_ms"] >= 0 and result["queue_wait_ms"] >= 0
