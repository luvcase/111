import queue
import signal
import threading
from urllib.parse import urlsplit

import httpx
from PIL import Image

from .images import encode_question_image


def enable_dpi_awareness():
    import sys

    if sys.platform == "win32":
        import ctypes

        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except (AttributeError, OSError):
            ctypes.windll.user32.SetProcessDPIAware()


def capture_image(monitor=1):
    import mss

    with mss.mss() as screen:
        if monitor < 1 or monitor >= len(screen.monitors):
            raise ValueError("显示器编号不存在，请从 1 开始选择")
        bounds = screen.monitors[monitor]
        shot = screen.grab(bounds)
        return Image.frombytes("RGB", shot.size, shot.rgb), dict(bounds)


def encode_image(image):
    return encode_question_image(image)


def capture(monitor=1):
    return encode_image(capture_image(monitor)[0])


def selection_box(start, end, width, height):
    x1, x2 = sorted((max(0, min(width, start[0])), max(0, min(width, end[0]))))
    y1, y2 = sorted((max(0, min(height, start[1])), max(0, min(height, end[1]))))
    if x2 - x1 < 5 or y2 - y1 < 5:
        return None
    return (x1, y1, x2, y2)


def validate_server(server):
    url = urlsplit(server)
    if url.scheme not in {"http", "https"} or not url.hostname:
        raise ValueError("请输入 http://电脑IP:8765 或 HTTPS 服务地址")
    if url.username or url.password or url.query or url.fragment or url.path not in {"", "/"}:
        raise ValueError("服务地址只能包含协议、主机和端口")
    return server.rstrip("/")


def run_desktop(
    server,
    token,
    hotkey="<ctrl>+<alt>+s",
    monitor=1,
    mode=None,
    region_hotkey="<ctrl>+<alt>+a",
    session_file=None,
):
    import tkinter as tk

    from PIL import ImageTk
    from pynput import keyboard, mouse

    enable_dpi_awareness()
    server = validate_server(server)
    credentials = {"token": token}

    def headers():
        return {"Authorization": "Bearer " + credentials["token"]}

    with httpx.Client(timeout=15, trust_env=False) as client:
        if session_file and session_file.exists():
            saved = session_file.read_text().strip()
            result = client.get(
                server + "/api/status", headers={"Authorization": "Bearer " + saved}
            )
            if result.status_code == 200:
                credentials["token"] = saved
        client.get(server + "/api/status", headers=headers()).raise_for_status()
    root = tk.Tk()
    root.withdraw()
    commands = queue.Queue(maxsize=8)
    stopped = threading.Event()
    busy = threading.Lock()
    preferences = {"right_hold": False}
    mouse_state = {"pressed": False, "serial": 0, "start": (0, 0)}
    overlay = {"window": None}

    def submit_command(kind, **kwargs):
        if not stopped.is_set():
            try:
                commands.put_nowait({"kind": kind, **kwargs})
            except queue.Full:
                pass

    def upload(jpeg):
        try:
            with httpx.Client(timeout=30, trust_env=False) as client:
                data = {"mode": mode} if mode else {}
                result = client.post(
                    server + "/api/screenshots",
                    headers=headers(),
                    files={"image": ("screen.jpg", jpeg, "image/jpeg")},
                    data=data,
                )
                result.raise_for_status()
            print("截图已发送，任务 " + result.json()["id"] + "。")
        except httpx.HTTPStatusError as exc:
            try:
                message = exc.response.json().get("detail", "请求失败")
            except ValueError:
                message = "请求失败"
            print(f"发送失败（{exc.response.status_code}）：{message}")
        except Exception:
            print("截图发送失败，请检查客户端连接及服务端模型配置。")
        finally:
            busy.release()

    def full_capture():
        if not busy.acquire(blocking=False):
            return

        def worker():
            try:
                jpeg = capture(monitor)
            except Exception as exc:
                print("截图失败：" + str(exc))
                busy.release()
                return
            upload(jpeg)

        threading.Thread(target=worker, daemon=True).start()

    def region_capture(right_start=None):
        if right_start and not mouse_state["pressed"]:
            return
        if not busy.acquire(blocking=False):
            return
        try:
            frozen, bounds = capture_image(monitor)
            window = tk.Toplevel(root)
            overlay["window"] = window
            window.overrideredirect(True)
            window.attributes("-topmost", True)
            window.geometry(f"{frozen.width}x{frozen.height}{bounds['left']:+}{bounds['top']:+}")
            canvas = tk.Canvas(
                window,
                width=frozen.width,
                height=frozen.height,
                highlightthickness=0,
                cursor="crosshair",
            )
            canvas.pack()
            photo = ImageTk.PhotoImage(frozen)
            canvas.create_image(0, 0, image=photo, anchor="nw")
            canvas.image = photo
            canvas.create_text(
                24,
                24,
                anchor="nw",
                fill="#21a878",
                text="拖动选区，松开发送 · Esc 取消",
                font=("Microsoft YaHei", 16),
            )
            selection = {"start": None, "rectangle": None, "closed": False}

            def cancel(event=None):
                if selection["closed"]:
                    return
                selection["closed"] = True
                window.destroy()
                overlay["window"] = None
                busy.release()

            def begin(event):
                selection["start"] = (event.x, event.y)
                if selection["rectangle"]:
                    canvas.delete(selection["rectangle"])
                selection["rectangle"] = canvas.create_rectangle(
                    event.x, event.y, event.x, event.y, outline="#21a878", width=3
                )

            def move(event):
                if selection["start"] and selection["rectangle"]:
                    canvas.coords(selection["rectangle"], *selection["start"], event.x, event.y)

            def finish(event):
                if selection["closed"] or not selection["start"]:
                    return
                box = selection_box(
                    selection["start"], (event.x, event.y), frozen.width, frozen.height
                )
                if not box:
                    cancel()
                    return
                selection["closed"] = True
                window.destroy()
                overlay["window"] = None
                jpeg = encode_image(frozen.crop(box))
                threading.Thread(target=upload, args=(jpeg,), daemon=True).start()

            canvas.bind("<ButtonPress-1>", begin)
            canvas.bind("<B1-Motion>", move)
            canvas.bind("<ButtonRelease-1>", finish)
            canvas.bind("<B3-Motion>", move)
            canvas.bind("<ButtonRelease-3>", finish)
            window.bind("<Escape>", cancel)
            window.focus_force()
            if right_start and mouse_state["pressed"]:
                x = right_start[0] - bounds["left"]
                y = right_start[1] - bounds["top"]
                if 0 <= x < frozen.width and 0 <= y < frozen.height:
                    begin(type("Point", (), {"x": x, "y": y})())
        except Exception as exc:
            if overlay["window"]:
                overlay["window"].destroy()
                overlay["window"] = None
            busy.release()
            print("区域截图失败：" + str(exc))

    def poll_server():
        with httpx.Client(timeout=5, trust_env=False) as client:
            while not stopped.is_set():
                try:
                    response = client.get(server + "/api/desktop/poll", headers=headers())
                    response.raise_for_status()
                    payload = response.json()
                    preferences.update(payload["workspace"])
                    for command in payload["commands"]:
                        if command["kind"] == "login":
                            credentials["token"] = command["token"]
                            if session_file:
                                import os

                                fd = os.open(
                                    session_file, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600
                                )
                                with os.fdopen(fd, "w") as file:
                                    file.write(command["token"])
                            print("截图客户端已绑定工作台账号。")
                        else:
                            submit_command(command["kind"], delay=command.get("delay", 0))
                except (httpx.RequestError, httpx.HTTPStatusError, ValueError):
                    pass
                stopped.wait(0.75)

    def on_click(x, y, button, pressed):
        if button != mouse.Button.right:
            return
        mouse_state["pressed"] = pressed
        mouse_state["serial"] += 1
        if pressed and preferences.get("right_hold") and not overlay["window"]:
            mouse_state["start"] = (x, y)
            serial = mouse_state["serial"]

            def held():
                if mouse_state["pressed"] and mouse_state["serial"] == serial:
                    submit_command("region", right_start=(x, y))

            timer = threading.Timer(0.6, held)
            timer.daemon = True
            timer.start()

    def pump():
        if stopped.is_set():
            root.quit()
            return
        try:
            while True:
                command = commands.get_nowait()
                action = (
                    full_capture
                    if command["kind"] == "full"
                    else lambda cmd=command: region_capture(cmd.get("right_start"))
                )
                root.after(round(command.get("delay", 0) * 1000), action)
        except queue.Empty:
            pass
        root.after(80, pump)

    key_listener = keyboard.GlobalHotKeys(
        {hotkey: lambda: submit_command("full"), region_hotkey: lambda: submit_command("region")}
    )
    mouse_listener = mouse.Listener(on_click=on_click)
    key_listener.start()
    mouse_listener.start()
    threading.Thread(target=poll_server, daemon=True).start()
    print(f"全屏截图：{hotkey}；区域截图：{region_hotkey}；右键长按在工作台中开启。")
    signal.signal(signal.SIGINT, lambda *_: stopped.set())
    root.after(80, pump)
    try:
        root.mainloop()
    finally:
        stopped.set()
        key_listener.stop()
        mouse_listener.stop()
        root.destroy()
