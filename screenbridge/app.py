import asyncio
import base64
import hmac
import io
import json
import re
import secrets
import sqlite3
import time
from collections import OrderedDict
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, File, Form, Header, HTTPException, Request, UploadFile, WebSocket
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from PIL import Image, ImageOps, UnidentifiedImageError
from pydantic import BaseModel, Field
from starlette.websockets import WebSocketDisconnect

from .images import encode_question_image, image_mime
from .provider import MODEL_PRESETS, MODES, AIProvider, ProviderError, Settings
from .store import Store

MAX_UPLOAD = 8 * 1024 * 1024
MAX_PIXELS = 24_000_000
STATIC = Path(__file__).parent / "static"
QUESTION_TYPES = {"auto", "choice", "visual", "code", "math", "shenlun"}


class ModelInput(BaseModel):
    id: str | None = None
    name: str = Field(min_length=1, max_length=60)
    base_url: str = Field(default="https://api.openai.com/v1", max_length=500)
    model: str = Field(min_length=1, max_length=100)
    api_key: str = Field(default="", max_length=1000)
    reasoning_effort: Literal["auto", "low", "medium", "high", "xhigh"] = "auto"
    max_output_tokens: int = Field(default=16384, ge=1024, le=32768)
    compact_output: bool = False
    qwen_thinking: Literal["auto", "off", "on"] = "auto"


class CatalogInput(BaseModel):
    id: str | None = None
    base_url: str = Field(default="https://api.openai.com/v1", max_length=500)
    api_key: str = Field(default="", max_length=1000)


class AccountInput(BaseModel):
    username: str = Field(min_length=3, max_length=60)
    password: str = Field(min_length=10, max_length=128)


class Options(BaseModel):
    model_ids: list[str] = Field(default_factory=lambda: ["default"], min_length=1, max_length=3)
    mode: str = "concise"
    prompt: str = Field(default="", max_length=4000)
    question_type: str = "auto"
    collaborate: bool = False


class Workspace(Options):
    right_hold: bool = False
    current_job_id: str | None = None
    custom_prompts: list[dict[str, str]] = Field(default_factory=list, max_length=10)


class TextInput(Options):
    text: str = Field(min_length=1, max_length=20000)


def normalize_image(raw: bytes) -> bytes:
    try:
        with Image.open(io.BytesIO(raw)) as source:
            if source.format not in {"PNG", "JPEG", "WEBP"}:
                raise ValueError("仅支持 PNG、JPEG、WebP 图片")
            if source.width * source.height > MAX_PIXELS:
                raise ValueError("图片像素过多，请缩小截图范围")
            source.load()
            image = ImageOps.exif_transpose(source).convert("RGB")
            return encode_question_image(image)
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise ValueError("图片无效或损坏") from exc


def create_app(settings=None, token=None, provider=None, data_dir: Path | None = None):
    settings = settings or Settings.from_env()
    token = token or secrets.token_urlsafe(24)
    if len(token) < 24:
        raise ValueError("配对令牌至少需要 24 个字符")
    store = Store(data_dir)
    histories = {}
    tasks = set()
    clients = {}
    native = {}
    login_attempts = {}
    semaphore = asyncio.Semaphore(3)

    def history_for(owner):
        if owner not in histories:
            history = OrderedDict()
            for job in sorted(store.all(owner, "jobs"), key=lambda item: item["created_at"]):
                if job["status"] in {"processing", "queued"}:
                    job.update(status="error", error="服务重启中断了此任务，请重新提交。")
                    store.put(owner, "jobs", job["id"], job)
                history[job["id"]] = job
            histories[owner] = history
        return histories[owner]

    def workspace_for(owner):
        return Workspace().model_dump() | store.get(owner, "workspace", "current", {})

    def models_for(owner):
        default = {
            "id": "default",
            "name": "默认模型",
            "base_url": settings.base_url,
            "model": settings.model,
            "api_key": settings.api_key,
            "reasoning_effort": settings.reasoning_effort,
            "max_output_tokens": settings.max_output_tokens,
            "compact_output": settings.compact_output,
            "qwen_thinking": settings.qwen_thinking,
        }
        result = {"default": default}
        result.update(
            {
                item["id"]: {
                    "reasoning_effort": "auto",
                    "max_output_tokens": 16384,
                    "compact_output": False,
                    "qwen_thinking": "auto",
                }
                | item
                for item in store.all(owner, "models")
            }
        )
        return result

    def safe_model(model):
        return {key: value for key, value in model.items() if key != "api_key"} | {
            "configured": bool(model["api_key"])
        }

    def owner_for(value):
        if not isinstance(value, str) or len(value) > 256:
            return None
        if hmac.compare_digest(value.encode(), token.encode()):
            return "device"
        return store.resolve(value)

    def authorize(header):
        value = header.removeprefix("Bearer ") if isinstance(header, str) else ""
        owner = owner_for(value)
        if not owner:
            raise HTTPException(401, "登录或配对已失效")
        return owner

    def event(owner, value):
        for queue, client_owner in tuple(clients.items()):
            if owner != client_owner:
                continue
            if queue.full():
                queue.get_nowait()
            queue.put_nowait(value)

    def publish(owner, job):
        store.put(owner, "jobs", job["id"], job)
        event(owner, {"type": "job", "job": json.loads(json.dumps(job))})

    def validate_options(owner, options):
        if options.mode not in MODES or options.question_type not in QUESTION_TYPES:
            raise HTTPException(400, "未知提示词风格或题型")
        if len(set(options.model_ids)) != len(options.model_ids):
            raise HTTPException(400, "不能重复选择同一模型配置")
        models = models_for(owner)
        if any(key not in models for key in options.model_ids):
            raise HTTPException(400, "所选模型配置不存在")
        return [models[key].copy() for key in options.model_ids]

    async def analyze_model(config, jpeg, text, options, operation):
        selected = provider or AIProvider(
            Settings(
                api_key=config["api_key"],
                base_url=config["base_url"],
                model=config["model"],
                demo=settings.demo,
                reasoning_effort=config["reasoning_effort"],
                max_output_tokens=config["max_output_tokens"],
                compact_output=config["compact_output"],
                qwen_thinking=config["qwen_thinking"],
            )
        )
        waiting = time.monotonic()
        async with semaphore:
            queue_ms = round((time.monotonic() - waiting) * 1000)
            if hasattr(selected, "analyze"):
                result = await selected.analyze(
                    jpeg, text, options.mode, options.prompt, operation, options.question_type
                )
                return result | {"queue_wait_ms": queue_ms}
            return {
                "question": text,
                "answer": await selected.answer(jpeg, options.mode),
                "steps": [],
                "cautions": [],
            }

    async def process(owner, job, jpeg, text, options, configs):
        job.update(status="processing")
        publish(owner, job)

        async def run_one(config, result):
            start = time.monotonic()
            result["status"] = "processing"
            try:
                data = await analyze_model(config, jpeg, text, options, job["operation"])
                result.update(data, status="done")
            except ProviderError as exc:
                result.update(status="error", error=str(exc))
            except Exception:
                result.update(status="error", error="模型处理失败，请检查配置后重试。")
            result["elapsed_ms"] = round((time.monotonic() - start) * 1000)
            if len(configs) > 1:
                publish(owner, job)

        try:
            await asyncio.gather(
                *[
                    run_one(config, result)
                    for config, result in zip(configs, job["results"], strict=True)
                ]
            )
            successes = [result for result in job["results"] if result["status"] == "done"]
            if options.collaborate and len(successes) > 1 and job["operation"] != "ocr":
                review = (
                    "对照下列独立分析，指出一致与分歧，核验关键步骤，给出综合建议。"
                    "不要仅按多数投票判定答案；无法确认的分歧写入 cautions。\n"
                    + json.dumps(successes, ensure_ascii=False)
                )
                try:
                    job["synthesis"] = await analyze_model(
                        configs[0], jpeg, review, options, "analyze"
                    )
                except ProviderError as exc:
                    job["synthesis_error"] = str(exc)
                except Exception:
                    job["synthesis_error"] = "综合建议生成失败，独立分析仍可查看。"
            job["status"] = "done" if successes else "error"
            job["partial"] = bool(successes) and len(successes) < len(configs)
            job["error"] = "" if successes else job["results"][0]["error"]
            job["answer"] = "\n\n".join(
                result["question"] if job["operation"] == "ocr" else result["answer"]
                for result in successes
            )
            job["completed_at"] = time.time()
            publish(owner, job)
        except asyncio.CancelledError:
            job.update(status="error", error="服务停止，任务中断，请重新提交。")
            publish(owner, job)
            raise

    def enqueue(owner, jpeg, text, options, operation="analyze"):
        configs = validate_options(owner, options)
        if (
            not settings.demo
            and provider is None
            and any(not config["api_key"] for config in configs)
        ):
            raise HTTPException(503, "所选模型缺少 API Key，请先编辑模型配置")
        jobs = history_for(owner)
        if (
            sum(job["status"] in {"queued", "processing"} for job in jobs.values()) >= 5
            or len(tasks) >= 20
        ):
            raise HTTPException(429, "任务队列已满，请等待当前任务完成")
        thumbnail = ""
        if jpeg:
            with Image.open(io.BytesIO(jpeg)) as image:
                image_info = {
                    "width": image.width,
                    "height": image.height,
                    "bytes": len(jpeg),
                    "mime": image_mime(jpeg),
                }
                image.thumbnail((640, 640))
                output = io.BytesIO()
                image.save(output, "JPEG", quality=65)
                thumbnail = "data:image/jpeg;base64," + base64.b64encode(output.getvalue()).decode()
        job = {
            "id": secrets.token_hex(8),
            "created_at": time.time(),
            "status": "queued",
            "mode": options.mode,
            "operation": operation,
            "question_type": options.question_type,
            "input_text": text,
            "thumbnail": thumbnail,
            "image_info": image_info if jpeg else None,
            "answer": "",
            "error": "",
            "results": [
                {
                    "model_id": config["id"],
                    "model_name": config["name"],
                    "requested_model": config["model"],
                    "reasoning_effort": config["reasoning_effort"],
                    "status": "queued",
                    "answer": "",
                    "question": "",
                    "steps": [],
                    "cautions": [],
                    "error": "",
                }
                for config in configs
            ],
        }
        jobs[job["id"]] = job
        if jpeg:
            store.put(owner, "images", job["id"], base64.b64encode(jpeg).decode())
        while len(jobs) > 30:
            oldest = next(
                key for key, value in jobs.items() if value["status"] in {"done", "error"}
            )
            del jobs[oldest]
            store.delete(owner, "jobs", oldest)
            store.delete(owner, "images", oldest)
        workspace = workspace_for(owner)
        workspace["current_job_id"] = job["id"]
        store.put(owner, "workspace", "current", workspace)
        publish(owner, job)
        event(owner, {"type": "workspace", "workspace": workspace})
        task = asyncio.create_task(process(owner, job, jpeg, text, options, configs))
        tasks.add(task)
        task.add_done_callback(tasks.discard)
        return json.loads(json.dumps(job))

    @asynccontextmanager
    async def lifespan(app):
        yield
        for task in list(tasks):
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        store.close()

    app = FastAPI(title="ScreenBridge", lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.pair_token, app.state.store = token, store
    app.state.jobs, app.state.tasks = history_for("device"), tasks
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.middleware("http")
    async def security_headers(request, call_next):
        if request.url.path == "/api/screenshots":
            size = request.headers.get("content-length", "0")
            if size.isdigit() and int(size) > MAX_UPLOAD + 128 * 1024:
                return JSONResponse({"detail": "图片不能超过 8 MB"}, status_code=413)
        response = await call_next(request)
        response.headers.update(
            {
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
                "Referrer-Policy": "no-referrer",
                "Content-Security-Policy": "default-src 'self'; script-src 'self'; "
                "style-src 'self'; "
                "connect-src 'self'; img-src 'self' data: blob:; frame-ancestors 'none'",
            }
        )
        return response

    @app.get("/")
    async def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/healthz")
    async def health():
        return {"status": "ok", "demo": settings.demo, "configured": bool(settings.api_key)}

    @app.get("/api/status")
    async def status(authorization: str | None = Header(default=None)):
        owner = authorize(authorization)
        return {
            "demo": settings.demo,
            "model": settings.model,
            "configured": bool(settings.api_key),
            "username": store.username(owner),
            "account": owner != "device",
            "desktop_connected": time.time() - native.get(owner, {}).get("seen", 0) < 10,
        }

    @app.get("/api/jobs")
    async def history(authorization: str | None = Header(default=None)):
        return {"jobs": list(history_for(authorize(authorization)).values())}

    @app.delete("/api/jobs/{job_id}")
    async def delete_job(job_id: str, authorization: str | None = Header(default=None)):
        owner = authorize(authorization)
        jobs = history_for(owner)
        if job_id not in jobs:
            raise HTTPException(404, "记录不存在")
        if jobs[job_id]["status"] in {"queued", "processing"}:
            raise HTTPException(409, "请等待任务完成再删除")
        del jobs[job_id]
        store.delete(owner, "jobs", job_id)
        store.delete(owner, "images", job_id)
        event(owner, {"type": "deleted", "id": job_id})
        workspace = workspace_for(owner)
        if workspace["current_job_id"] == job_id:
            workspace["current_job_id"] = next(reversed(jobs), None)
            store.put(owner, "workspace", "current", workspace)
            event(owner, {"type": "workspace", "workspace": workspace})
        return {"ok": True}

    @app.get("/api/jobs/{job_id}/image")
    async def original_image(job_id: str, authorization: str | None = Header(default=None)):
        owner = authorize(authorization)
        raw = store.get(owner, "images", job_id)
        if not raw:
            raise HTTPException(404, "题图不存在")
        data = base64.b64decode(raw)
        return Response(data, media_type=image_mime(data))

    @app.get("/api/workspace")
    async def get_workspace(authorization: str | None = Header(default=None)):
        return workspace_for(authorize(authorization))

    @app.put("/api/workspace")
    async def put_workspace(value: Workspace, authorization: str | None = Header(default=None)):
        owner = authorize(authorization)
        validate_options(owner, value)
        if value.current_job_id and value.current_job_id not in history_for(owner):
            raise HTTPException(400, "复盘记录不存在")
        if any(
            len(item.get("name", "")) > 60 or len(item.get("text", "")) > 4000
            for item in value.custom_prompts
        ):
            raise HTTPException(400, "提示词名称或内容过长")
        data = value.model_dump()
        store.put(owner, "workspace", "current", data)
        event(owner, {"type": "workspace", "workspace": data})
        return data

    @app.get("/api/models")
    async def get_models(authorization: str | None = Header(default=None)):
        return {
            "models": [safe_model(item) for item in models_for(authorize(authorization)).values()],
            "presets": MODEL_PRESETS,
        }

    @app.post("/api/model-catalog")
    async def model_catalog(value: CatalogInput, authorization: str | None = Header(default=None)):
        owner = authorize(authorization)
        config = models_for(owner).get(value.id) if value.id else None
        api_key = value.api_key
        if not api_key and config and config["base_url"].rstrip("/") == value.base_url.rstrip("/"):
            api_key = config["api_key"]
        try:
            selected = AIProvider(Settings(api_key=api_key, base_url=value.base_url.rstrip("/")))
            async with semaphore:
                available = await selected.list_models()
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except ProviderError as exc:
            raise HTTPException(502, str(exc)) from exc
        return {
            "models": available,
            "message": "列表来自当前 Key；题图分析仍需模型支持图片与 Chat Completions。",
        }

    @app.post("/api/models")
    async def save_model(value: ModelInput, authorization: str | None = Header(default=None)):
        owner = authorize(authorization)
        current = models_for(owner)
        key = value.id or secrets.token_hex(8)
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", key):
            raise HTTPException(400, "配置 ID 无效")
        if key not in current and len(current) >= 10:
            raise HTTPException(400, "最多保存 10 套模型配置")
        previous = current.get(key, {})
        if (
            not value.api_key
            and previous.get("api_key")
            and previous.get("base_url", "").rstrip("/") != value.base_url.rstrip("/")
        ):
            raise HTTPException(
                400, "更换 API 地址时请重新填写 Key，避免将已有密钥发送到其他服务。"
            )
        api_key = value.api_key or previous.get("api_key", "")
        try:
            Settings(base_url=value.base_url, model=value.model)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        model = {
            "id": key,
            "name": value.name,
            "model": value.model,
            "base_url": value.base_url.rstrip("/"),
            "api_key": api_key,
            "reasoning_effort": value.reasoning_effort,
            "max_output_tokens": value.max_output_tokens,
            "compact_output": value.compact_output,
            "qwen_thinking": value.qwen_thinking,
        }
        store.put(owner, "models", key, model)
        event(owner, {"type": "models"})
        return safe_model(model)

    @app.delete("/api/models/{model_id}")
    async def delete_model(model_id: str, authorization: str | None = Header(default=None)):
        owner = authorize(authorization)
        if model_id == "default":
            raise HTTPException(400, "默认配置可编辑，不能删除")
        store.delete(owner, "models", model_id)
        workspace = workspace_for(owner)
        workspace["model_ids"] = [key for key in workspace["model_ids"] if key != model_id] or [
            "default"
        ]
        store.put(owner, "workspace", "current", workspace)
        event(owner, {"type": "models"})
        event(owner, {"type": "workspace", "workspace": workspace})
        return {"ok": True}

    @app.post("/api/models/{model_id}/test")
    async def test_model(model_id: str, authorization: str | None = Header(default=None)):
        owner = authorize(authorization)
        config = models_for(owner).get(model_id)
        if not config:
            raise HTTPException(404, "模型配置不存在")
        selected = AIProvider(
            Settings(
                api_key=config["api_key"],
                base_url=config["base_url"],
                model=config["model"],
                demo=settings.demo,
                reasoning_effort=config["reasoning_effort"],
                max_output_tokens=config["max_output_tokens"],
                compact_output=config["compact_output"],
                qwen_thinking=config["qwen_thinking"],
            )
        )
        try:
            async with semaphore:
                return await selected.test_connection()
        except ProviderError as exc:
            raise HTTPException(502, str(exc)) from exc

    @app.post("/api/analyze", status_code=202)
    async def text_analyze(value: TextInput, authorization: str | None = Header(default=None)):
        owner = authorize(authorization)
        if not value.text.strip():
            raise HTTPException(400, "请输入题目")
        return enqueue(owner, None, value.text, value)

    @app.post("/api/screenshots", status_code=202)
    async def upload(
        image: UploadFile = File(...),
        mode: str | None = Form(None),
        model_ids: str | None = Form(None),
        text: str = Form(""),
        prompt: str | None = Form(None),
        question_type: str | None = Form(None),
        operation: str = Form("analyze"),
        collaborate: bool | None = Form(None),
        authorization: str | None = Header(default=None),
    ):
        owner = authorize(authorization)
        data = workspace_for(owner)
        for key, value in {
            "mode": mode,
            "prompt": prompt,
            "question_type": question_type,
            "collaborate": collaborate,
        }.items():
            if value is not None:
                data[key] = value
        if model_ids is not None:
            try:
                data["model_ids"] = json.loads(model_ids)
            except ValueError as exc:
                raise HTTPException(400, "模型列表无效") from exc
        try:
            options = Options.model_validate(data)
        except ValueError as exc:
            raise HTTPException(400, "分析参数无效") from exc
        validate_options(owner, options)
        if operation not in {"analyze", "ocr"} or len(text) > 20000:
            raise HTTPException(400, "操作或题目长度无效")
        raw = await image.read(MAX_UPLOAD + 1)
        await image.close()
        if len(raw) > MAX_UPLOAD:
            raise HTTPException(413, "图片不能超过 8 MB")
        try:
            jpeg = normalize_image(raw)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return enqueue(owner, jpeg, text, options, operation)

    @app.post("/api/auth/register")
    async def register(value: AccountInput, authorization: str | None = Header(default=None)):
        authorize(authorization)
        username = value.username.strip().lower()
        if not re.fullmatch(r"[a-z0-9_.@-]{3,60}", username):
            raise HTTPException(400, "账号请使用 3–60 位英文、数字或 _.@-")
        try:
            owner = await asyncio.to_thread(store.register, username, value.password)
        except sqlite3.IntegrityError as exc:
            raise HTTPException(409, "账号已存在") from exc
        return {"token": store.session(owner), "username": username}

    @app.post("/api/auth/login")
    async def login(value: AccountInput, request: Request):
        ip = request.client.host if request.client else "unknown"
        attempts = [moment for moment in login_attempts.get(ip, []) if moment > time.time() - 300]
        if len(attempts) >= 10:
            raise HTTPException(429, "登录过于频繁，请 5 分钟后重试")
        if len(login_attempts) > 1000:
            login_attempts.clear()
        login_attempts[ip] = attempts + [time.time()]
        owner = await asyncio.to_thread(store.login, value.username.strip().lower(), value.password)
        if not owner:
            raise HTTPException(401, "账号或密码错误")
        return {"token": store.session(owner), "username": store.username(owner)}

    @app.post("/api/auth/logout")
    async def logout(authorization: str | None = Header(default=None)):
        owner = authorize(authorization)
        if owner != "device":
            store.revoke(authorization.removeprefix("Bearer "))
        return {"ok": True}

    @app.post("/api/desktop/capture")
    async def capture_command(request: Request, authorization: str | None = Header(default=None)):
        owner = authorize(authorization)
        payload = await request.json()
        if not isinstance(payload, dict) or payload.get("kind") not in {"full", "region"}:
            raise HTTPException(400, "截图类型无效")
        agent = native.get(owner, {})
        if time.time() - agent.get("seen", 0) > 10:
            raise HTTPException(503, "请先启动电脑截图客户端，并登录相同账号或设备空间")
        if len(agent["commands"]) >= 2:
            raise HTTPException(429, "已有待执行截图")
        agent["commands"].append({"kind": payload["kind"], "delay": 2})
        return {"ok": True}

    @app.post("/api/desktop/link")
    async def link_desktop(authorization: str | None = Header(default=None)):
        owner = authorize(authorization)
        if owner == "device":
            raise HTTPException(400, "请先登录账号")
        agent = native.get("device", {})
        if time.time() - agent.get("seen", 0) > 10:
            raise HTTPException(503, "请启动设备空间中的电脑客户端，或使用 desktop --login")
        agent["commands"] = [{"kind": "login", "token": store.session(owner)}]
        return {"ok": True}

    @app.get("/api/desktop/poll")
    async def poll(authorization: str | None = Header(default=None)):
        owner = authorize(authorization)
        agent = native.setdefault(owner, {"seen": 0, "commands": []})
        agent["seen"] = time.time()
        commands, agent["commands"] = agent["commands"], []
        return {"commands": commands, "workspace": workspace_for(owner)}

    @app.websocket("/ws")
    async def socket(websocket: WebSocket):
        await websocket.accept()
        try:
            message = await asyncio.wait_for(websocket.receive_json(), timeout=10)
            incoming = message.get("token", "") if isinstance(message, dict) else ""
            owner = owner_for(incoming)
            if not owner:
                await websocket.close(code=4401)
                return
        except WebSocketDisconnect:
            return
        except (TimeoutError, ValueError):
            await websocket.close(code=4401)
            return
        queue = asyncio.Queue(maxsize=128)
        clients[queue] = owner
        try:
            await websocket.send_json(
                {
                    "type": "snapshot",
                    "jobs": list(history_for(owner).values()),
                    "workspace": workspace_for(owner),
                }
            )
            while True:
                try:
                    value = await asyncio.wait_for(queue.get(), timeout=10)
                except TimeoutError:
                    value = {"type": "ping"}
                if not owner_for(incoming):
                    await websocket.close(code=4401)
                    return
                await websocket.send_json(value)
        except (WebSocketDisconnect, RuntimeError, OSError):
            pass
        finally:
            clients.pop(queue, None)

    return app
