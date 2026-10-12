import io
import threading
import time
from urllib.parse import urlsplit

import httpx
from PIL import Image


def capture(monitor=1):
    import mss

    with mss.mss() as screen:
        if monitor < 1 or monitor >= len(screen.monitors):
            raise ValueError("显示器编号不存在，请从 1 开始选择")
        shot = screen.grab(screen.monitors[monitor])
        image = Image.frombytes("RGB", shot.size, shot.rgb)
        image.thumbnail((1920, 1920))
        output = io.BytesIO()
        image.save(output, format="JPEG", quality=85)
        return output.getvalue()


def validate_server(server):
    url = urlsplit(server)
    if url.scheme not in {"http", "https"} or not url.hostname:
        raise ValueError("请输入 http://电脑IP:8765 或 HTTPS 服务地址")
    if url.username or url.password or url.query or url.fragment or url.path not in {"", "/"}:
        raise ValueError("服务地址只能包含协议、主机和端口")
    return server.rstrip("/")


def run_desktop(server, token, hotkey="<ctrl>+<alt>+s", monitor=1, mode="concise"):
    from pynput import keyboard

    server = validate_server(server)
    headers = {"Authorization": "Bearer " + token}
    with httpx.Client(timeout=15, trust_env=False) as client:
        response = client.get(server + "/api/status", headers=headers)
        response.raise_for_status()
    print("已连接。截图快捷键：" + hotkey + "；Ctrl+C 退出。")
    lock = threading.Lock()
    last_capture = 0.0

    def upload():
        nonlocal last_capture
        try:
            jpeg = capture(monitor)
            with httpx.Client(timeout=30, trust_env=False) as client:
                result = client.post(
                    server + "/api/screenshots",
                    headers=headers,
                    files={"image": ("screen.jpg", jpeg, "image/jpeg")},
                    data={"mode": mode},
                )
                result.raise_for_status()
            print("截图已发送，任务 " + result.json()["id"] + "。请查看手机。")
        except httpx.HTTPStatusError as exc:
            try:
                detail = exc.response.json().get("detail", "请求失败")
            except ValueError:
                detail = "请求失败"
            print(f"发送失败（{exc.response.status_code}）：{detail}")
        except httpx.RequestError:
            print("发送失败：无法连接服务，请检查电脑端服务。")
        except Exception as exc:
            print("截图失败：" + str(exc))
        finally:
            last_capture = time.monotonic()
            lock.release()

    def on_hotkey():
        if time.monotonic() - last_capture < 1 or not lock.acquire(blocking=False):
            return
        threading.Thread(target=upload, daemon=True).start()

    with keyboard.GlobalHotKeys({hotkey: on_hotkey}) as listener:
        try:
            listener.join()
        except KeyboardInterrupt:
            listener.stop()
