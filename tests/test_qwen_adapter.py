import base64
import io
import json

import httpx
import pytest
from PIL import Image, ImageDraw

from screenbridge.app import normalize_image
from screenbridge.desktop import encode_image
from screenbridge.provider import AIProvider, ProviderError, Settings


def grid_image():
    image = Image.new("RGB", (360, 240), "white")
    draw = ImageDraw.Draw(image)
    for x in range(20, 320, 11):
        draw.line((x, 20, x, 220), fill="black", width=1)
    for y in range(20, 220, 11):
        draw.line((20, y, 320, y), fill="black", width=1)
    draw.rectangle((42, 42, 52, 52), fill="black")
    return image


def test_screenshot_and_server_preserve_thin_grid_lines_losslessly():
    original = grid_image()
    captured = encode_image(original)
    uploaded = normalize_image(captured)
    with Image.open(io.BytesIO(uploaded)) as image:
        assert image.format == "PNG"
        assert image.size == original.size
        assert image.tobytes() == original.tobytes()


@pytest.mark.parametrize("thinking", ["auto", "off", "on"])
async def test_qwen_image_task_budget_and_diagnostics(monkeypatch, thinking):
    image = encode_image(grid_image())

    def handler(request):
        payload = json.loads(request.content)
        assert payload["model"] == "qwen3.8-omni-flash"
        assert payload["max_tokens"] == 2048
        assert "reasoning_effort" not in payload
        if thinking == "auto":
            assert "enable_thinking" not in payload
        else:
            assert payload["enable_thinking"] is (thinking == "on")
        content = payload["messages"][1]["content"]
        assert "请完成图片中的题目并给出最终答案" in content[0]["text"]
        assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")
        assert base64.b64decode(content[1]["image_url"]["url"].split(",")[1]) == image
        assert "简短输出" in payload["messages"][0]["content"]
        return httpx.Response(
            200,
            headers={"x-request-id": "request-fixture-123"},
            json={
                "model": "qwen3.8-omni-flash",
                "usage": {
                    "prompt_tokens": 1200,
                    "completion_tokens": 80,
                    "completion_tokens_details": {"reasoning_tokens": 20},
                },
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "content": json.dumps(
                                {
                                    "question": "图形题",
                                    "answer": "答案：B",
                                    "steps": [],
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
            api_key="private-fixture",
            base_url="https://ws-fixture.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
            model="qwen3.8-omni-flash",
            max_output_tokens=2048,
            compact_output=True,
            qwen_thinking=thinking,
        )
    )
    result = await provider.analyze(image, "", "visual")
    diagnostics = result["diagnostics"]
    assert result["answer"] == "答案：B"
    assert diagnostics["model_confirmed"] and diagnostics["output_budget"] == 2048
    assert diagnostics["request_id"] == "request-fixture-123"
    assert diagnostics["input_tokens"] == 1200 and diagnostics["reasoning_tokens"] == 20
    assert "private-fixture" not in json.dumps(result)


async def test_free_tier_403_is_not_mislabeled_as_permission_failure(monkeypatch):
    def handler(request):
        return httpx.Response(
            403,
            json={"error": {"code": "AllocationQuota.FreeTierOnly", "message": "private-fixture"}},
        )

    original = httpx.AsyncClient
    monkeypatch.setattr(
        "screenbridge.provider.httpx.AsyncClient",
        lambda **kw: original(transport=httpx.MockTransport(handler), **kw),
    )
    provider = AIProvider(Settings(api_key="private-fixture"))
    with pytest.raises(ProviderError, match="免费额度") as error:
        await provider.test_connection()
    assert "private-fixture" not in str(error.value)


async def test_missing_response_model_is_marked_unconfirmed(monkeypatch):
    def handler(request):
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"answer":"2"}'}}]})

    original = httpx.AsyncClient
    monkeypatch.setattr(
        "screenbridge.provider.httpx.AsyncClient",
        lambda **kw: original(transport=httpx.MockTransport(handler), **kw),
    )
    provider = AIProvider(Settings(api_key="fixture"))
    result = await provider.analyze(None, "1+1", "concise")
    assert not result["diagnostics"]["model_confirmed"]
