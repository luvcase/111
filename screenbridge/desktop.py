import json
import queue
import signal
import sys
import threading
import time
from urllib.parse import urlsplit

import httpx
from PIL import Image

from .capture_panel import CapturePanel
from .images import encode_question_image, image_mime


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

    if sys.platform == "darwin":
        from .macos import permission_status

        if permission_status()["screen_recording"] is not True:
            raise RuntimeError(
                "无法确认屏幕录制权限，请在启动窗口打开 Mac 截图权限设置，授权后重启。"
            )
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


def pixel_selection(box, logical_size, pixel_size):
    """Map an overlay selection to the original capture, including Retina scaling."""
    from math import ceil, floor

    sx, sy = pixel_size[0] / logical_size[0], pixel_size[1] / logical_size[1]
    return (
        max(0, floor(box[0] * sx)),
        max(0, floor(box[1] * sy)),
        min(pixel_size[0], ceil(box[2] * sx)),
        min(pixel_size[1], ceil(box[3] * sy)),
    )


def validate_server(server):
    url = urlsplit(server)
    if url.scheme not in {"http", "https"} or not url.hostname:
        raise ValueError("请输入 http://电脑IP:8765 或 HTTPS 服务地址")
    if url.username or url.password or url.query or url.fragment or url.path not in {"", "/"}:
        raise ValueError("服务地址只能包含协议、主机和端口")
    return server.rstrip("/")


def shortcut_bindings(hotkey, region_hotkey, submit, report):
    """Keep translated UI labels out of the command queue."""

    def trigger(kind, label):
        report(last_shortcut=label, shortcut_at=time.time())
        submit(kind)

    return {
        hotkey: lambda: trigger("full", "全屏"),
        region_hotkey: lambda: trigger("region", "区域"),
    }


def capture_action(command, full_capture, region_capture):
    kind = command["kind"]
    if kind == "full":
        return full_capture
    if kind == "region":
        return lambda: region_capture(command.get("right_start"))
    raise ValueError(f"未知截图指令：{str(kind)[:40]}")


def create_key_listener(keyboard, hotkeys):
    if sys.platform == "win32":
        from .windows import WindowsHotKeys

        # Escape belongs to the focused region selector, not to every app globally.
        return WindowsHotKeys({key: action for key, action in hotkeys.items() if key != "<esc>"})
    if sys.platform == "darwin":
        from .macos import hotkey_listener

        return hotkey_listener(keyboard, hotkeys)
    return keyboard.GlobalHotKeys(hotkeys)


def run_desktop(
    server,
    token,
    hotkey="<alt>+s",
    monitor=1,
    mode=None,
    region_hotkey="<ctrl>+<alt>+a",
    session_file=None,
):
    import tkinter as tk

    from PIL import ImageTk

    listen = True
    if sys.platform == "darwin":
        from .macos import permission_status

        listen = permission_status()["accessibility"] is True
    if listen:
        from pynput import keyboard, mouse

    enable_dpi_awareness()
    server = validate_server(server)
    credentials = {"token": token}
    diagnostic_lock = threading.Lock()
    diagnostics = {"hotkeys": "未启动", "last_shortcut": "尚未收到", "last_capture": "尚未截图"}

    def diagnose(**values):
        with diagnostic_lock:
            diagnostics.update(values, updated_at=time.time())
            if session_file:
                try:
                    path = session_file.parent / "desktop-status.json"
                    temporary = path.with_suffix(".tmp")
                    temporary.write_text(
                        json.dumps(diagnostics, ensure_ascii=False), encoding="utf-8"
                    )
                    temporary.replace(path)
                except OSError:
                    print("无法保存截图诊断。")

    if sys.platform == "darwin":
        diagnose(permissions=permission_status())

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
    ui_events = queue.SimpleQueue()
    stopped = threading.Event()
    busy = threading.Lock()
    preferences = {"right_hold": False}
    mouse_state = {"pressed": False, "serial": 0, "start": (0, 0)}
    overlay = {"window": None, "cancel": None}

    def submit_command(kind, **kwargs):
        if kind not in {"full", "region", "cancel", "panel"}:
            diagnose(last_error=f"未知截图指令：{str(kind)[:40]}")
            return
        if not stopped.is_set():
            try:
                commands.put_nowait({"kind": kind, **kwargs})
            except queue.Full:
                diagnose(last_error="截图请求队列已满，请稍后重试。")

    panel = CapturePanel(root, submit_command)

    def upload(jpeg):
        try:
            with httpx.Client(timeout=30, trust_env=False) as client:
                data = {"mode": mode} if mode else {}
                result = client.post(
                    server + "/api/screenshots",
                    headers=headers(),
                    files={
                        "image": (
                            "screen.png" if image_mime(jpeg) == "image/png" else "screen.jpg",
                            jpeg,
                            image_mime(jpeg),
                        )
                    },
                    data=data,
                )
                result.raise_for_status()
            print("截图已发送，任务 " + result.json()["id"] + "。")
            diagnose(last_capture="截图已发送", last_error="")
        except httpx.HTTPStatusError as exc:
            try:
                message = exc.response.json().get("detail", "请求失败")
            except ValueError:
                message = "请求失败"
            print(f"发送失败（{exc.response.status_code}）：{message}")
            diagnose(last_capture="发送失败", last_error=str(message)[:500])
        except Exception:
            print("截图发送失败，请检查客户端连接及服务端模型配置。")
            diagnose(last_capture="发送失败", last_error="请检查客户端连接及服务端模型配置。")
        finally:
            busy.release()

    def full_capture():
        if overlay["cancel"]:
            overlay["cancel"]()
        if not busy.acquire(blocking=False):
            diagnose(last_error="上一张截图仍在处理，本次全屏截图未执行，请稍后再按。")
            return
        diagnose(last_capture="正在全屏截图", last_error="")
        panel.suspend()

        def worker():
            try:
                jpeg = capture(monitor)
            except Exception as exc:
                print("截图失败：" + str(exc))
                diagnose(last_capture="截图失败", last_error=str(exc)[:500])
                busy.release()
                return
            finally:
                ui_events.put(panel.resume)
            upload(jpeg)

        def start_worker():
            try:
                threading.Thread(target=worker, daemon=True).start()
            except Exception as exc:
                busy.release()
                panel.resume()
                diagnose(last_capture="截图失败", last_error=str(exc)[:500])

        # Give the window compositor time to remove the keypad from the screen.
        root.after(200, start_worker)

    def region_capture(right_start=None):
        if right_start and not mouse_state["pressed"]:
            return
        if not busy.acquire(blocking=False):
            diagnose(last_error="已有截图任务，请先完成或取消当前选区。")
            return
        diagnose(last_capture="正在准备区域截图", last_error="")
        panel.suspend()
        root.after(200, lambda: open_region(right_start))

    def open_region(right_start=None):
        try:
            frozen, bounds = capture_image(monitor)
            logical_size = (bounds["width"], bounds["height"])
            displayed = frozen.resize(logical_size, Image.Resampling.LANCZOS)
            window = tk.Toplevel(root)
            overlay["window"] = window
            window.overrideredirect(True)
            window.attributes("-topmost", True)
            window.geometry(
                f"{logical_size[0]}x{logical_size[1]}{bounds['left']:+}{bounds['top']:+}"
            )
            canvas = tk.Canvas(
                window,
                width=logical_size[0],
                height=logical_size[1],
                highlightthickness=0,
                cursor="crosshair",
            )
            canvas.pack()
            photo = ImageTk.PhotoImage(displayed)
            canvas.create_image(0, 0, image=photo, anchor="nw")
            canvas.image = photo
            canvas.create_text(
                24,
                24,
                anchor="nw",
                fill="#21a878",
                text="拖动选区，松开发送 · Esc 取消",
                font=("PingFang SC" if sys.platform == "darwin" else "Microsoft YaHei", 16),
            )
            selection = {"start": None, "rectangle": None, "closed": False, "timeout": None}

            def close_overlay():
                if selection["closed"]:
                    return False
                selection["closed"] = True
                if selection["timeout"]:
                    try:
                        root.after_cancel(selection["timeout"])
                    except tk.TclError:
                        pass
                overlay["window"] = None
                overlay["cancel"] = None
                try:
                    window.destroy()
                except tk.TclError:
                    pass
                panel.resume()
                return True

            def cancel(event=None, reason="区域选框已取消", error=""):
                if close_overlay():
                    busy.release()
                    diagnose(last_capture=reason, last_error=error)

            overlay["cancel"] = cancel

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
                box = selection_box(selection["start"], (event.x, event.y), *logical_size)
                if not box:
                    cancel()
                    return
                try:
                    jpeg = encode_image(
                        frozen.crop(pixel_selection(box, logical_size, frozen.size))
                    )
                except Exception as exc:
                    cancel(reason="区域截图失败", error=str(exc)[:500])
                    return
                if close_overlay():
                    diagnose(last_capture="正在发送区域截图", last_error="")
                    try:
                        threading.Thread(target=upload, args=(jpeg,), daemon=True).start()
                    except Exception as exc:
                        busy.release()
                        diagnose(last_capture="区域截图发送失败", last_error=str(exc)[:500])

            canvas.bind("<ButtonPress-1>", begin)
            canvas.bind("<B1-Motion>", move)
            canvas.bind("<ButtonRelease-1>", finish)
            canvas.bind("<B3-Motion>", move)
            canvas.bind("<ButtonRelease-3>", finish)
            window.bind("<Escape>", cancel)
            window.protocol("WM_DELETE_WINDOW", cancel)
            window.bind("<Destroy>", lambda event: cancel() if event.widget is window else None)
            selection["timeout"] = root.after(
                60000, lambda: cancel(reason="区域选框等待超过60秒，已取消")
            )
            window.focus_force()
            diagnose(last_capture="正在区域选框", last_error="")
            if right_start and mouse_state["pressed"]:
                x = right_start[0] - bounds["left"]
                y = right_start[1] - bounds["top"]
                if 0 <= x < logical_size[0] and 0 <= y < logical_size[1]:
                    begin(type("Point", (), {"x": x, "y": y})())
        except Exception as exc:
            if overlay["cancel"]:
                overlay["cancel"](reason="区域截图失败", error=str(exc)[:500])
            else:
                if overlay["window"]:
                    overlay["window"].destroy()
                    overlay["window"] = None
                busy.release()
                panel.resume()
            print("区域截图失败：" + str(exc))
            diagnose(last_capture="区域截图失败", last_error=str(exc)[:500])

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

    listener_health = {"alive": None}

    def pump():
        if stopped.is_set():
            root.quit()
            return
        if key_listener is not None:
            alive = key_listener.is_alive()
            if alive != listener_health["alive"]:
                listener_health["alive"] = alive
                diagnose(
                    hotkeys=getattr(
                        key_listener, "status", "监听线程运行中（请查看是否收到快捷键）"
                    )
                    if alive
                    else getattr(key_listener, "status", "监听线程已停止，请退出重开软件并检查权限")
                )
        try:
            while not ui_events.empty():
                ui_events.get_nowait()()
            while True:
                command = commands.get_nowait()
                if command["kind"] == "panel":
                    panel.show()
                    continue
                if command["kind"] == "cancel":
                    if overlay["cancel"]:
                        overlay["cancel"]()
                    continue
                if command.get("source") == "panel":
                    diagnose(last_input="虚拟按键：" + command["kind"])
                try:
                    action = capture_action(command, full_capture, region_capture)
                except ValueError as exc:
                    diagnose(last_error=str(exc))
                    continue
                root.after(round(command.get("delay", 0) * 1000), action)
        except queue.Empty:
            pass
        with diagnostic_lock:
            panel.update_status(diagnostics)
        root.after(80, pump)

    key_listener = mouse_listener = None
    if listen:
        hotkeys = shortcut_bindings(hotkey, region_hotkey, submit_command, diagnose)
        hotkeys["<esc>"] = lambda: submit_command("cancel")
        try:
            key_listener = create_key_listener(keyboard, hotkeys)
            key_listener.start()
            diagnose(hotkeys=getattr(key_listener, "status", "监听已启动，请查看最后收到的快捷键"))
        except Exception as exc:
            if key_listener:
                key_listener.stop()
            key_listener = None
            diagnose(hotkeys="快捷键启动失败：" + str(exc)[:300])
        mouse_listener = mouse.Listener(on_click=on_click)
        mouse_listener.start()
    else:
        print("未获得辅助功能权限：全局快捷键和右键监听未开启，授权后请重启软件。")
        diagnose(hotkeys="启动时未获辅助功能权限，监听未开启；请退出整个软件后重新打开")
    threading.Thread(target=poll_server, daemon=True).start()
    print(f"全屏截图：{hotkey}；区域截图：{region_hotkey}；右键长按在工作台中开启。")
    signal.signal(signal.SIGINT, lambda *_: stopped.set())
    root.after(80, pump)
    try:
        root.mainloop()
    finally:
        stopped.set()
        if key_listener:
            key_listener.stop()
        if mouse_listener:
            mouse_listener.stop()
        root.destroy()
