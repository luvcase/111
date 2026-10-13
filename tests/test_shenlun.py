import json
import time

import httpx
from fastapi.testclient import TestClient

from screenbridge.app import create_app
from screenbridge.provider import Settings


def test_shenlun_workspace_and_formal_answer_flow(monkeypatch):
    requests = []
    answer = "1.拓宽参与渠道，建立居民议事会。\n2.完善落实机制，及时回应居民诉求。"

    def handler(request):
        payload = json.loads(request.content)
        instructions = payload["messages"][0]["content"]
        requests.append(payload)
        assert "没有统一的万能标准格式" in instructions
        assert "关键材料不足" in instructions
        assert payload["messages"][1]["content"][0]["text"].endswith("不超过200字。")
        return httpx.Response(
            200,
            json={
                "model": "gpt-6-astra",
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "content": json.dumps(
                                {
                                    "question": "概括社区治理做法",
                                    "answer": answer,
                                    "steps": ["按参与渠道和落实机制分类，核对材料要点。"],
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
    token = "shenlun-test-device-token-12345"
    headers = {"Authorization": "Bearer " + token}
    with TestClient(create_app(Settings(api_key="fixture", model="gpt-6-astra"), token)) as client:
        prefs = client.put(
            "/api/workspace", headers=headers, json={"mode": "shenlun", "question_type": "shenlun"}
        )
        assert prefs.status_code == 200
        response = client.post(
            "/api/analyze",
            headers=headers,
            json={
                "mode": "shenlun",
                "question_type": "shenlun",
                "text": "材料：社区建立议事会，及时回应诉求。请概括主要做法，不超过200字。",
            },
        )
        assert response.status_code == 202
        for _ in range(100):
            job = client.get("/api/jobs", headers=headers).json()["jobs"][0]
            if job["status"] in {"done", "error"}:
                break
            time.sleep(0.01)
        assert job["status"] == "done"
        assert job["results"][0]["answer"] == answer
        assert job["results"][0]["question"] == "概括社区治理做法"
        assert job["results"][0]["steps"] and len(requests) == 1
