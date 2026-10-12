import asyncio
import io

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from screenbridge.app import MAX_UPLOAD, create_app, normalize_image
from screenbridge.provider import AIProvider, ProviderError, Settings

TOKEN = "test-pairing-token-for-local-tests"
HEADERS = {"Authorization": "Bearer " + TOKEN}


def picture():
    output = io.BytesIO()
    Image.new("RGB", (120, 80), "white").save(output, format="PNG")
    return output.getvalue()


class FakeProvider:
    async def answer(self, image, mode):
        assert Image.open(io.BytesIO(image)).format == "JPEG"
        assert mode == "code"
        return "答案是 42。\n```python\nprint(42)\n```"


def test_upload_push_and_reconnect():
    app = create_app(Settings(api_key="test-only"), TOKEN, FakeProvider())
    with TestClient(app) as client:
        with client.websocket_connect("/ws") as socket:
            socket.send_json({"token": TOKEN})
            snapshot = socket.receive_json()
            assert snapshot["type"] == "snapshot"
            assert snapshot["jobs"] == []
            response = client.post(
                "/api/screenshots",
                headers=HEADERS,
                files={"image": ("x.png", picture())},
                data={"mode": "code"},
            )
            assert response.status_code == 202
            statuses = []
            while not statuses or statuses[-1] != "done":
                event = socket.receive_json()
                if event["type"] == "job":
                    statuses.append(event["job"]["status"])
            assert statuses == ["queued", "processing", "done"]
        with client.websocket_connect("/ws") as socket:
            socket.send_json({"token": TOKEN})
            job = socket.receive_json()["jobs"][0]
            assert job["answer"].startswith("答案是 42")
            assert job["status"] == "done"
        assert client.get("/api/jobs", headers=HEADERS).json()["jobs"][0] == job


def test_auth_and_rejected_inputs():
    with TestClient(create_app(Settings(demo=True), TOKEN)) as client:
        assert client.get("/healthz").json()["status"] == "ok"
        assert client.get("/api/jobs").status_code == 401
        assert (
            client.get("/api/status", headers={"Authorization": "Bearer wrong"}).status_code == 401
        )
        assert client.post("/api/screenshots", files={"image": ("x", picture())}).status_code == 401
        assert (
            client.post(
                "/api/screenshots", headers=HEADERS, files={"image": ("x", b"bad")}
            ).status_code
            == 400
        )
        assert (
            client.post(
                "/api/screenshots",
                headers=HEADERS,
                files={"image": ("x", picture())},
                data={"mode": "invalid"},
            ).status_code
            == 400
        )
        assert (
            client.post(
                "/api/screenshots", headers=HEADERS, files={"image": ("x", b"x" * (MAX_UPLOAD + 1))}
            ).status_code
            == 413
        )
        with client.websocket_connect("/ws") as socket:
            socket.send_json({"token": "wrong"})
            assert socket.receive()["code"] == 4401


def test_no_key_is_not_ready_for_real_ai():
    with TestClient(create_app(Settings(), TOKEN)) as client:
        assert (
            client.post(
                "/api/screenshots", headers=HEADERS, files={"image": ("x", picture())}
            ).status_code
            == 503
        )


async def test_queue_bound_and_provider_failure():
    release = asyncio.Event()

    class SlowProvider:
        async def answer(self, image, mode):
            await release.wait()
            raise ProviderError("模拟 AI 限流")

    app = create_app(Settings(api_key="test-only"), TOKEN, SlowProvider())
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            for _ in range(5):
                response = await client.post(
                    "/api/screenshots", headers=HEADERS, files={"image": ("x", picture())}
                )
                assert response.status_code == 202
            response = await client.post(
                "/api/screenshots", headers=HEADERS, files={"image": ("x", picture())}
            )
            assert response.status_code == 429
            release.set()
            await asyncio.gather(*app.state.tasks)
            assert all(job["status"] == "error" for job in app.state.jobs.values())
            assert all(job["error"] == "模拟 AI 限流" for job in app.state.jobs.values())


def test_image_normalization_and_pixel_limit():
    assert Image.open(io.BytesIO(normalize_image(picture()))).format == "JPEG"
    output = io.BytesIO()
    Image.new("RGB", (6000, 5000)).save(output, format="PNG")
    with pytest.raises(ValueError, match="像素"):
        normalize_image(output.getvalue())


async def test_openai_request_contract_and_error_redaction(monkeypatch):
    captured = []

    def handler(request):
        import json

        body = json.loads(request.content)
        captured.append(body)
        assert request.headers["Authorization"] == "Bearer test-secret"
        assert body["messages"][1]["content"][1]["image_url"]["url"].startswith(
            "data:image/jpeg;base64,"
        )
        return httpx.Response(200, json={"choices": [{"message": {"content": "识别后的答案"}}]})

    client_class = httpx.AsyncClient
    monkeypatch.setattr(
        "screenbridge.provider.httpx.AsyncClient",
        lambda **kw: client_class(transport=httpx.MockTransport(handler), **kw),
    )
    provider = AIProvider(Settings(api_key="test-secret"))
    assert await provider.answer(b"image", "concise") == "识别后的答案"
    assert captured[0]["model"] == "gpt-4.1-mini"

    def bad_handler(request):
        return httpx.Response(401, text="private server text test-secret")

    monkeypatch.setattr(
        "screenbridge.provider.httpx.AsyncClient",
        lambda **kw: client_class(transport=httpx.MockTransport(bad_handler), **kw),
    )
    with pytest.raises(ProviderError, match="API Key 无效") as error:
        await provider.answer(b"image", "concise")
    assert "test-secret" not in str(error.value)


def test_insecure_provider_address_rejected():
    with pytest.raises(ValueError, match="HTTPS"):
        Settings(base_url="http://api.example.com/v1")
