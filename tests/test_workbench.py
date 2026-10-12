import asyncio
import json

import httpx
from fastapi.testclient import TestClient
from test_app import HEADERS, TOKEN, picture

from screenbridge.app import create_app
from screenbridge.desktop import selection_box
from screenbridge.provider import AIProvider, ProviderError, Settings, parse_analysis


def register(client, name):
    response = client.post(
        "/api/auth/register",
        headers=HEADERS,
        json={"username": name, "password": "test-password-123"},
    )
    assert response.status_code == 200
    return {"Authorization": "Bearer " + response.json()["token"]}


def test_models_keep_secrets_server_side_and_preserve_key():
    app = create_app(Settings(demo=True), TOKEN)
    with TestClient(app) as client:
        response = client.post(
            "/api/models",
            headers=HEADERS,
            json={"name": "视觉模型", "model": "vision-test", "api_key": "private-fixture-key"},
        )
        model = response.json()
        assert response.status_code == 200 and model["configured"]
        assert "private-fixture-key" not in response.text
        assert "api_key" not in model
        response = client.post(
            "/api/models", headers=HEADERS, json={**model, "name": "更新名称", "api_key": ""}
        )
        assert response.json()["configured"]
        owner = app.state.store.get("device", "models", model["id"])
        assert owner["api_key"] == "private-fixture-key"
        assert "private-fixture-key" not in client.get("/api/models", headers=HEADERS).text
        result = client.post(f"/api/models/{model['id']}/test", headers=HEADERS)
        assert result.json()["demo"] and "未验证" in result.json()["message"]
        response = client.post(
            "/api/models",
            headers=HEADERS,
            json={"name": "bad", "model": "bad", "base_url": "https://127.0.0.1"},
        )
        assert response.status_code == 400


async def test_parallel_models_and_collaborative_synthesis(monkeypatch):
    started = set()
    both_started = asyncio.Event()
    calls = []

    async def analyze(self, image, text, mode, prompt, operation, question_type):
        calls.append((self.settings.model, text))
        if "对照下列独立分析" not in text:
            started.add(self.settings.model)
            if len(started) == 2:
                both_started.set()
            await asyncio.wait_for(both_started.wait(), 2)
        return {
            "question": text,
            "answer": self.settings.model + " 的答案",
            "steps": ["关键推导"],
            "cautions": ["核验条件"],
        }

    monkeypatch.setattr(AIProvider, "analyze", analyze)
    app = create_app(Settings(api_key="fixture-key"), TOKEN)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://test"
        ) as client:
            model = (
                await client.post(
                    "/api/models",
                    headers=HEADERS,
                    json={"name": "模型 B", "model": "model-b", "api_key": "fixture-key"},
                )
            ).json()
            response = await client.post(
                "/api/analyze",
                headers=HEADERS,
                json={
                    "text": "1+1 等于多少",
                    "model_ids": ["default", model["id"]],
                    "collaborate": True,
                    "mode": "explain",
                },
            )
            assert response.status_code == 202
            await asyncio.wait_for(asyncio.gather(*app.state.tasks), 3)
            job = app.state.jobs[response.json()["id"]]
            assert job["status"] == "done" and len(job["results"]) == 2
            assert all(r["status"] == "done" for r in job["results"])
            assert len(calls) == 3 and "synthesis" in job
            assert "对照下列独立分析" in calls[-1][1]


async def test_partial_failure_does_not_drop_success(monkeypatch):
    async def analyze(self, *args):
        if self.settings.model == "bad-model":
            raise ProviderError("模型限流")
        return {"question": "题目", "answer": "可用答案", "steps": [], "cautions": []}

    monkeypatch.setattr(AIProvider, "analyze", analyze)
    app = create_app(Settings(api_key="fixture-key"), TOKEN)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://test"
        ) as client:
            bad = (
                await client.post(
                    "/api/models",
                    headers=HEADERS,
                    json={"name": "失效模型", "model": "bad-model", "api_key": "fixture-key"},
                )
            ).json()
            response = await client.post(
                "/api/analyze",
                headers=HEADERS,
                json={"text": "问题", "model_ids": ["default", bad["id"]]},
            )
            await asyncio.gather(*app.state.tasks)
            job = app.state.jobs[response.json()["id"]]
            assert job["status"] == "done" and job["partial"]
            assert job["answer"] == "可用答案"
            assert job["results"][1]["error"] == "模型限流"


def test_account_isolation_sync_images_logout_and_restart(tmp_path):
    app = create_app(Settings(demo=True), TOKEN, data_dir=tmp_path)
    with TestClient(app) as client:
        alice, bob = register(client, "alice"), register(client, "bob")
        login = client.post(
            "/api/auth/login", json={"username": "alice", "password": "test-password-123"}
        )
        second = {"Authorization": "Bearer " + login.json()["token"]}
        with client.websocket_connect("/ws") as socket:
            socket.send_json({"token": second["Authorization"].removeprefix("Bearer ")})
            assert socket.receive_json()["jobs"] == []
            response = client.post(
                "/api/screenshots", headers=alice, files={"image": ("q.png", picture())}
            )
            job_id = response.json()["id"]
            while True:
                event = socket.receive_json()
                if event["type"] == "job" and event["job"]["status"] == "done":
                    assert event["job"]["results"][0]["steps"]
                    break
        assert len(client.get("/api/jobs", headers=second).json()["jobs"]) == 1
        assert client.get("/api/jobs", headers=bob).json()["jobs"] == []
        assert client.get(f"/api/jobs/{job_id}/image", headers=second).status_code == 200
        assert client.get(f"/api/jobs/{job_id}/image", headers=bob).status_code == 404
        assert client.delete(f"/api/jobs/{job_id}", headers=bob).status_code == 404
        assert client.post("/api/auth/logout", headers=alice).status_code == 200
        assert client.get("/api/jobs", headers=alice).status_code == 401
        assert client.get("/api/jobs", headers=second).status_code == 200
    restarted = create_app(Settings(demo=True), TOKEN, data_dir=tmp_path)
    with TestClient(restarted) as client:
        assert client.get("/api/jobs", headers=second).json()["jobs"][0]["id"] == job_id
        assert client.delete(f"/api/jobs/{job_id}", headers=second).status_code == 200
        assert client.get(f"/api/jobs/{job_id}/image", headers=second).status_code == 404
        assert client.get("/api/workspace", headers=second).json()["current_job_id"] is None


async def test_ocr_separate_from_analysis_and_persisted_preferences(monkeypatch):
    calls = []

    async def analyze(self, image, text, mode, prompt, operation, question_type):
        calls.append((image, mode, prompt, operation, question_type))
        return {"question": "原始题干\nA. 1\nB. 2", "answer": "", "steps": [], "cautions": []}

    monkeypatch.setattr(AIProvider, "analyze", analyze)
    app = create_app(Settings(api_key="fixture"), TOKEN)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://test"
        ) as client:
            response = await client.put(
                "/api/workspace",
                headers=HEADERS,
                json={
                    "mode": "choice",
                    "question_type": "choice",
                    "prompt": "保留选项",
                    "right_hold": True,
                    "custom_prompts": [{"name": "我的风格", "text": "保留选项"}],
                },
            )
            assert response.status_code == 200
            response = await client.post(
                "/api/screenshots",
                headers=HEADERS,
                files={"image": ("q.png", picture())},
                data={"operation": "ocr"},
            )
            await asyncio.gather(*app.state.tasks)
            job = app.state.jobs[response.json()["id"]]
            assert job["answer"] == "原始题干\nA. 1\nB. 2"
            assert calls[0][0] and calls[0][1:] == ("choice", "保留选项", "ocr", "choice")
            prefs = (await client.get("/api/workspace", headers=HEADERS)).json()
            assert prefs["current_job_id"] == job["id"] and prefs["right_hold"]
            assert prefs["custom_prompts"][0]["name"] == "我的风格"


def test_desktop_commands_and_account_binding():
    with TestClient(create_app(Settings(demo=True), TOKEN)) as client:
        assert (
            client.post(
                "/api/desktop/capture", headers=HEADERS, json={"kind": "region"}
            ).status_code
            == 503
        )
        assert client.get("/api/desktop/poll", headers=HEADERS).json()["commands"] == []
        assert (
            client.post(
                "/api/desktop/capture", headers=HEADERS, json={"kind": "region"}
            ).status_code
            == 200
        )
        assert (
            client.get("/api/desktop/poll", headers=HEADERS).json()["commands"][0]["kind"]
            == "region"
        )
        alice = register(client, "alice")
        assert client.post("/api/desktop/link", headers=alice).status_code == 200
        link = client.get("/api/desktop/poll", headers=HEADERS).json()["commands"][0]
        assert link["kind"] == "login"
        account_headers = {"Authorization": "Bearer " + link["token"]}
        client.get("/api/desktop/poll", headers=account_headers)
        assert client.get("/api/status", headers=alice).json()["desktop_connected"]
        assert (
            client.post("/api/desktop/capture", headers=alice, json={"kind": "full"}).status_code
            == 200
        )
        assert client.get("/api/desktop/poll", headers=HEADERS).json()["commands"] == []
        assert (
            client.get("/api/desktop/poll", headers=account_headers).json()["commands"][0]["kind"]
            == "full"
        )


def test_region_selection_clamps_and_rejects_clicks():
    assert selection_box((80, 70), (-10, 20), 100, 60) == (0, 20, 80, 60)
    assert selection_box((5, 5), (7, 7), 100, 100) is None


def test_structured_results_and_fallback():
    data = {"question": "题目", "answer": "42", "steps": ["计算"], "cautions": ["边界"]}
    assert parse_analysis("```json\n" + json.dumps(data) + "\n```") == data
    assert parse_analysis("原始答案")["answer"] == "原始答案"
    assert parse_analysis("转录文字", "ocr")["question"] == "转录文字"
    assert parse_analysis('{"steps": "bad"}')["cautions"]


async def test_real_connection_test_contract(monkeypatch):
    def handler(request):
        body = json.loads(request.content)
        assert body["model"] == "vision-test"
        assert body["max_completion_tokens"] == 16
        return httpx.Response(200, json={"choices": [{"message": {"content": "OK"}}]})

    original = httpx.AsyncClient
    monkeypatch.setattr(
        "screenbridge.provider.httpx.AsyncClient",
        lambda **kw: original(transport=httpx.MockTransport(handler), **kw),
    )
    result = await AIProvider(Settings(api_key="fixture", model="vision-test")).test_connection()
    assert result["ok"] and not result["demo"] and result["reply"] == "OK"


async def test_real_visual_and_ocr_request_contract(monkeypatch):
    import base64

    from screenbridge.app import normalize_image

    jpeg = normalize_image(picture())
    calls = []

    def handler(request):
        body = json.loads(request.content)
        messages = body["messages"]
        assert base64.b64decode(messages[1]["content"][1]["image_url"]["url"].split(",")[1]) == jpeg
        calls.append(messages[0]["content"])
        ocr = "仅准确转录" in messages[0]["content"]
        data = {
            "question": "选项 A 和 B",
            "answer": "" if ocr else "选择 A",
            "steps": [] if ocr else ["排除 B"],
            "cautions": ["图片需清晰"],
        }
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(data)}}]})

    original = httpx.AsyncClient
    monkeypatch.setattr(
        "screenbridge.provider.httpx.AsyncClient",
        lambda **kw: original(transport=httpx.MockTransport(handler), **kw),
    )
    model = AIProvider(Settings(api_key="fixture-key"))
    analysis = await model.analyze(jpeg, "", "choice", "说明排除依据", "analyze", "choice")
    assert analysis["answer"] == "选择 A" and analysis["steps"] == ["排除 B"]
    ocr = await model.analyze(jpeg, "", "concise", operation="ocr")
    assert ocr["question"] == "选项 A 和 B" and not ocr["answer"]
    assert "说明排除依据" in calls[0] and "仅准确转录" in calls[1]
