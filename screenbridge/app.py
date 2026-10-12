import asyncio
import hmac
import io
import secrets
import time
from collections import OrderedDict
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Form, Header, HTTPException, UploadFile, WebSocket
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image, ImageOps, UnidentifiedImageError
from starlette.websockets import WebSocketDisconnect

from .provider import MODES, AIProvider, ProviderError, Settings

MAX_UPLOAD = 8 * 1024 * 1024
MAX_PIXELS = 24_000_000
STATIC = Path(__file__).parent / "static"


def normalize_image(raw: bytes) -> bytes:
    try:
        with Image.open(io.BytesIO(raw)) as source:
            if source.format not in {"PNG", "JPEG", "WEBP"}:
                raise ValueError("仅支持 PNG、JPEG、WebP 图片")
            if source.width * source.height > MAX_PIXELS:
                raise ValueError("图片像素过多，请缩小截图范围")
            source.load()
            image = ImageOps.exif_transpose(source).convert("RGB")
            image.thumbnail((1920, 1920))
            output = io.BytesIO()
            image.save(output, format="JPEG", quality=85)
            return output.getvalue()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise ValueError("图片无效或损坏") from exc


def create_app(settings: Settings | None = None, token: str | None = None, provider=None):
    settings = settings or Settings.from_env()
    token = token or secrets.token_urlsafe(24)
    if len(token) < 24:
        raise ValueError("配对令牌至少需要 24 个字符")
    jobs: OrderedDict[str, dict] = OrderedDict()
    tasks: set[asyncio.Task] = set()
    clients: set[asyncio.Queue] = set()
    semaphore = asyncio.Semaphore(1)
    provider = provider or AIProvider(settings)

    @asynccontextmanager
    async def lifespan(app):
        yield
        for task in list(tasks):
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    app = FastAPI(title="ScreenBridge", lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.pair_token = token
    app.state.jobs = jobs
    app.state.tasks = tasks
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.middleware("http")
    async def security_headers(request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'"
        )
        return response

    def authorize(header):
        expected = "Bearer " + token
        if not isinstance(header, str) or not hmac.compare_digest(
            header.encode("utf-8"), expected.encode("utf-8")
        ):
            raise HTTPException(401, "配对令牌无效")

    def publish(job):
        for queue in tuple(clients):
            if queue.full():
                queue.get_nowait()
            queue.put_nowait({"type": "job", "job": dict(job)})

    async def process(job, jpeg):
        async with semaphore:
            job.update(status="processing")
            publish(job)
            try:
                answer = await provider.answer(jpeg, job["mode"])
                job.update(status="done", answer=answer)
            except ProviderError as exc:
                job.update(status="error", error=str(exc))
            except Exception:
                # Never forward provider response bodies or credential-bearing exceptions.
                job.update(status="error", error="处理失败，请检查服务端日志配置或重试。")
            job["completed_at"] = time.time()
            publish(job)

    @app.get("/")
    async def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/healthz")
    async def health():
        return {"status": "ok", "demo": settings.demo, "configured": bool(settings.api_key)}

    @app.get("/api/status")
    async def status(authorization: str | None = Header(default=None)):
        authorize(authorization)
        return {
            "demo": settings.demo,
            "model": settings.model,
            "configured": bool(settings.api_key),
        }

    @app.get("/api/jobs")
    async def history(authorization: str | None = Header(default=None)):
        authorize(authorization)
        return {"jobs": list(jobs.values())}

    @app.post("/api/screenshots", status_code=202)
    async def upload(
        image: UploadFile = File(...),
        mode: str = Form("concise"),
        authorization: str | None = Header(default=None),
    ):
        authorize(authorization)
        if mode not in MODES:
            raise HTTPException(400, "未知回答模式")
        raw = await image.read(MAX_UPLOAD + 1)
        await image.close()
        if len(raw) > MAX_UPLOAD:
            raise HTTPException(413, "图片不能超过 8 MB")
        if sum(job["status"] in {"queued", "processing"} for job in jobs.values()) >= 5:
            raise HTTPException(429, "最多同时处理 5 张截图，请等待当前任务完成")
        try:
            jpeg = normalize_image(raw)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        if not settings.demo and not settings.api_key:
            raise HTTPException(503, "尚未配置 AI Key，请配置 .env 后重启")
        job = {
            "id": secrets.token_hex(8),
            "created_at": time.time(),
            "status": "queued",
            "mode": mode,
            "answer": "",
            "error": "",
        }
        jobs[job["id"]] = job
        while len(jobs) > 30:
            oldest = next(
                key for key, value in jobs.items() if value["status"] in {"done", "error"}
            )
            del jobs[oldest]
        publish(job)
        task = asyncio.create_task(process(job, jpeg))
        tasks.add(task)
        task.add_done_callback(tasks.discard)
        return dict(job)

    @app.websocket("/ws")
    async def socket(websocket: WebSocket):
        await websocket.accept()
        try:
            message = await asyncio.wait_for(websocket.receive_json(), timeout=10)
            incoming = message.get("token", "") if isinstance(message, dict) else ""
            if not isinstance(incoming, str) or not hmac.compare_digest(
                incoming.encode("utf-8"), token.encode("utf-8")
            ):
                await websocket.close(code=4401)
                return
        except WebSocketDisconnect:
            return
        except (TimeoutError, ValueError):
            await websocket.close(code=4401)
            return
        queue: asyncio.Queue = asyncio.Queue(maxsize=128)
        clients.add(queue)
        try:
            await websocket.send_json({"type": "snapshot", "jobs": list(jobs.values())})
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15)
                except TimeoutError:
                    event = {"type": "ping"}
                await websocket.send_json(event)
        except (WebSocketDisconnect, RuntimeError, OSError):
            pass
        finally:
            clients.discard(queue)

    return app
