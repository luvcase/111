import io
import json

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from screenbridge.app import create_app, normalize_image
from screenbridge.desktop import encode_image
from screenbridge.images import encode_question_image
from screenbridge.provider import AIProvider, Settings

TOKEN = "catalog-test-device-token-12345"
HEADERS = {"Authorization": "Bearer " + TOKEN}


def test_catalog_uses_saved_key_without_returning_it(monkeypatch):
    calls = []

    def handler(request):
        calls.append((request.method, str(request.url), request.headers["authorization"]))
        return httpx.Response(
            200,
            json={"data": [{"id": "gpt-6-astra"}, {"id": "gpt-6.1-sol"}, {"id": "bad\nvalue"}, {}]},
        )

    original = httpx.AsyncClient
    monkeypatch.setattr(
        "screenbridge.provider.httpx.AsyncClient",
        lambda **kw: original(transport=httpx.MockTransport(handler), **kw),
    )
    app = create_app(Settings(api_key="saved-private-fixture"), TOKEN)
    with TestClient(app) as client:
        assert client.post("/api/model-catalog", json={"id": "default"}).status_code == 401
        response = client.post("/api/model-catalog", headers=HEADERS, json={"id": "default"})
        assert response.status_code == 200
        assert response.json()["models"] == ["gpt-6-astra", "gpt-6.1-sol"]
        assert "saved-private-fixture" not in response.text
        # An edited URL must not receive the credential retained for a different service.
        response = client.post(
            "/api/model-catalog",
            headers=HEADERS,
            json={"id": "default", "base_url": "https://another.example.com/v1"},
        )
        assert response.status_code == 502
        assert len(calls) == 1
    assert calls == [("GET", "https://api.openai.com/v1/models", "Bearer saved-private-fixture")]


def test_selected_model_and_effort_survive_restart(tmp_path):
    settings = Settings(api_key="saved-fixture")
    app = create_app(settings, TOKEN, data_dir=tmp_path)
    with TestClient(app) as client:
        response = client.post(
            "/api/models",
            headers=HEADERS,
            json={
                "id": "default",
                "name": "Astra",
                "model": "gpt-6-astra",
                "reasoning_effort": "high",
                "max_output_tokens": 32768,
            },
        )
        assert response.status_code == 200
        assert response.json()["configured"]
        assert (
            client.post(
                "/api/models",
                headers=HEADERS,
                json={"name": "bad", "model": "gpt-6-astra", "reasoning_effort": "invalid"},
            ).status_code
            == 422
        )
    with TestClient(create_app(settings, TOKEN, data_dir=tmp_path)) as client:
        model = client.get("/api/models", headers=HEADERS).json()["models"][0]
        assert model["model"] == "gpt-6-astra" and model["reasoning_effort"] == "high"
        assert model["max_output_tokens"] == 32768 and model["configured"]


@pytest.mark.parametrize("encode", [normalize_image, encode_image])
def test_4k_question_image_keeps_native_resolution(encode):
    picture = Image.new("RGB", (3840, 2160), "white")
    raw = io.BytesIO()
    picture.save(raw, "PNG")
    argument = raw.getvalue() if encode is normalize_image else picture
    with Image.open(io.BytesIO(encode(argument))) as image:
        assert image.size == (3840, 2160)


def test_textured_screenshots_fit_upload_limit_without_modifying_source():
    picture = Image.effect_noise((512, 384), 100).convert("RGB")
    encoded = encode_question_image(picture, byte_limit=20000)
    assert len(encoded) <= 20000
    assert picture.size == (512, 384)
    with Image.open(io.BytesIO(encoded)) as image:
        assert abs(image.width / image.height - 4 / 3) < 0.02


async def test_analysis_sends_high_detail_and_reports_response_model(monkeypatch):
    def handler(request):
        body = json.loads(request.content)
        assert body["model"] == "gpt-6.1-sol"
        assert body["reasoning_effort"] == "high"
        assert body["max_completion_tokens"] == 32768
        assert body["messages"][1]["content"][1]["image_url"]["detail"] == "high"
        return httpx.Response(
            200,
            json={
                "model": "gpt-6.1-sol-snapshot",
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "content": json.dumps(
                                {
                                    "question": "1+1",
                                    "answer": "2",
                                    "steps": ["复核：2-1=1"],
                                    "cautions": [],
                                }
                            )
                        },
                    }
                ],
            },
        )

    original = httpx.AsyncClient
    monkeypatch.setattr(
        "screenbridge.provider.httpx.AsyncClient",
        lambda **kw: original(transport=httpx.MockTransport(handler), **kw),
    )
    provider = AIProvider(
        Settings(
            api_key="fixture", model="gpt-6.1-sol", reasoning_effort="high", max_output_tokens=32768
        )
    )
    result = await provider.analyze(b"fixture-image", "", "explain")
    assert result["api_model"] == "gpt-6.1-sol-snapshot" and result["answer"] == "2"
