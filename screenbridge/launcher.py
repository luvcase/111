"""Desktop application entry point; the frozen desktop bundle needs no system Python."""

import argparse
import io
import json
import os
import secrets
import socket
import subprocess
import sys
import tempfile
import threading
import time
import webbrowser
from pathlib import Path
from urllib.parse import quote

import httpx
import uvicorn
from PIL import Image

from .app import create_app
from .cli import local_ip, pairing_files
from .provider import Settings


def data_directory():
    if sys.platform == "win32":
        base = Path(os.getenv("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.getenv("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    directory = base / "ScreenBridge"
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    return directory


def redirect_windowed_logs(directory, name):
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Child processes can receive real stdout handles using a non-Chinese Windows locale.
    # Normalize those handles too; windowed executables do not always expose None streams.
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")
    if sys.stdout is None or sys.stderr is None:
        log = (directory / name).open("a", encoding="utf-8", buffering=1)
        if sys.stdout is None:
            sys.stdout = log
        if sys.stderr is None:
            sys.stderr = log


class AppLock:
    def __init__(self, directory):
        self.file = (directory / "app.lock").open("a+b")
        if self.file.tell() == 0:
            self.file.write(b"0")
            self.file.flush()
        self.file.seek(0)
        self.acquired = False
        try:
            if sys.platform == "win32":
                import msvcrt

                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.acquired = True
        except OSError:
            self.file.close()

    def close(self):
        if self.acquired:
            if sys.platform == "win32":
                import msvcrt

                self.file.seek(0)
                msvcrt.locking(self.file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.file.fileno(), fcntl.LOCK_UN)
            self.file.close()
            self.acquired = False


def child_command(directory, port):
    flags = ["--native", "--data-dir", str(directory), "--port", str(port)]
    if getattr(sys, "frozen", False):
        return [sys.executable, *flags]
    return [sys.executable, "-m", "screenbridge.launcher", *flags]


class RuntimeController:
    def __init__(self, directory, demo=False, port=8765):
        self.directory = Path(directory)
        self.demo, self.requested_port = demo, port
        self.server = self.thread = self.socket = self.child = self.child_log = None
        self.port = None
        self.token = None

    @property
    def base_url(self):
        return f"http://127.0.0.1:{self.port}"

    @property
    def workbench_url(self):
        return self.base_url + "/#token=" + quote(self.active_token())

    def active_token(self):
        session_file = self.directory / "desktop-session.txt"
        if session_file.exists():
            candidate = session_file.read_text().strip()
            try:
                with httpx.Client(timeout=1, trust_env=False) as client:
                    response = client.get(
                        self.base_url + "/api/status",
                        headers={"Authorization": "Bearer " + candidate},
                    )
                    if response.status_code == 200:
                        return candidate
            except httpx.RequestError:
                pass
        return self.token

    def start(self, native=True):
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.server is not None:
            raise RuntimeError("应用已经启动")
        candidates = (
            [0]
            if self.requested_port == 0
            else range(self.requested_port, self.requested_port + 20)
        )
        for port in candidates:
            bound = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                bound.bind(("0.0.0.0", port))
                bound.listen(128)
                bound.setblocking(False)
                self.socket = bound
                break
            except OSError:
                bound.close()
        if not self.socket:
            raise RuntimeError("无法打开本机服务端口，请关闭重复的程序后重试。")
        self.port = self.socket.getsockname()[1]
        self.token = secrets.token_urlsafe(24)
        app = create_app(Settings.from_env(demo=self.demo), self.token, data_dir=self.directory)
        config = uvicorn.Config(
            app,
            host="0.0.0.0",
            port=self.port,
            loop="asyncio",
            http="h11",
            ws="websockets",
            log_level="warning",
            access_log=False,
            timeout_graceful_shutdown=3,
        )
        self.server = uvicorn.Server(config)
        phone_url = f"http://{local_ip()}:{self.port}/#token={quote(self.token)}"
        pairing_files(self.directory, self.token, phone_url)
        self.thread = threading.Thread(
            target=self.server.run, kwargs={"sockets": [self.socket]}, daemon=True
        )
        self.thread.start()
        headers = {"Authorization": "Bearer " + self.token}
        deadline = time.monotonic() + 15
        with httpx.Client(timeout=1, trust_env=False) as client:
            while time.monotonic() < deadline:
                if not self.thread.is_alive():
                    raise RuntimeError("本机服务启动失败，请查看应用日志。")
                try:
                    if (
                        client.get(self.base_url + "/api/status", headers=headers).status_code
                        == 200
                    ):
                        break
                except httpx.RequestError:
                    pass
                time.sleep(0.1)
            else:
                raise RuntimeError("本机服务启动超时。")
            if native:
                self.child_log = (self.directory / "desktop.log").open("a", encoding="utf-8")
                self.child = subprocess.Popen(
                    child_command(self.directory, self.port),
                    stdout=self.child_log,
                    stderr=self.child_log,
                    creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
                )
                while time.monotonic() < deadline:
                    if self.child.poll() is not None:
                        raise RuntimeError("截图客户端启动失败，请查看 desktop.log。")
                    try:
                        status = client.get(
                            self.base_url + "/api/status",
                            headers={"Authorization": "Bearer " + self.active_token()},
                        ).json()
                        if status["desktop_connected"]:
                            break
                    except (httpx.RequestError, ValueError):
                        pass
                    time.sleep(0.15)
                else:
                    raise RuntimeError("截图客户端连接超时。")
        (self.directory / "instance.json").write_text(
            json.dumps({"port": self.port}), encoding="utf-8"
        )

    def stop(self):
        if self.child:
            if self.child.poll() is None:
                self.child.terminate()
                try:
                    self.child.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.child.kill()
                    self.child.wait(timeout=2)
            self.child = None
        if self.child_log:
            self.child_log.close()
            self.child_log = None
        if self.server:
            self.server.should_exit = True
        if self.thread:
            self.thread.join(timeout=8)
            if self.thread.is_alive():
                raise RuntimeError("服务尚未退出，请稍后重试。")
        if self.socket:
            self.socket.close()
        self.server = self.thread = self.socket = None
        instance = self.directory / "instance.json"
        instance.unlink(missing_ok=True)


def package_smoke(result_path, native=None):
    result = {"ok": False, "native_checked": False}
    controller = None
    temporary = None

    def checkpoint(stage):
        result["stage"] = stage
        result_path.parent.mkdir(parents=True, exist_ok=True)
        result_path.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")

    try:
        checkpoint("initializing_tk")
        import tkinter as tk

        native = sys.platform == "win32" if native is None else native
        if native or sys.platform == "darwin":
            root = tk.Tk()
            root.withdraw()
            root.update()
            root.destroy()
            result["tk_checked"] = True
        temporary = tempfile.TemporaryDirectory(prefix="screenbridge-package-")
        controller = RuntimeController(Path(temporary.name), demo=True, port=0)
        checkpoint("starting_runtime")
        controller.start(native=native)
        checkpoint("checking_capture")
        headers = {"Authorization": "Bearer " + controller.token}
        with httpx.Client(timeout=5, trust_env=False) as client:
            page = client.get(controller.base_url + "/")
            assert page.status_code == 200 and "ScreenBridge" in page.text
            assert client.get(controller.base_url + "/static/app.js").status_code == 200
            if native:
                response = client.post(
                    controller.base_url + "/api/desktop/capture",
                    headers=headers,
                    json={"kind": "full"},
                )
                assert response.status_code == 200
                job_id = None
            else:
                picture = io.BytesIO()
                Image.new("RGB", (120, 80), "white").save(picture, format="PNG")
                response = client.post(
                    controller.base_url + "/api/screenshots",
                    headers=headers,
                    files={"image": ("smoke.png", picture.getvalue(), "image/png")},
                )
                assert response.status_code == 202
                job_id = response.json()["id"]
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                jobs = client.get(controller.base_url + "/api/jobs", headers=headers).json()["jobs"]
                if jobs:
                    job = next((j for j in jobs if j["id"] == job_id), jobs[-1])
                    if job["status"] == "done":
                        assert "未调用 AI" in job["answer"]
                        assert job["thumbnail"].startswith("data:image/jpeg;base64,")
                        break
                    if job["status"] == "error":
                        raise RuntimeError("截图传输检查未完成")
                time.sleep(0.2)
            else:
                raise RuntimeError("打包后的截图流程检查超时")
            result.update(
                ok=True, native_checked=native, frozen=bool(getattr(sys, "frozen", False))
            )
        controller.stop()
        controller = None
        checkpoint("complete")
    except Exception as exc:
        result["error"] = str(exc)
        if controller:
            for filename in ("desktop.log", "native-startup-error.log"):
                log_file = controller.directory / filename
                if log_file.exists():
                    result[filename] = log_file.read_text(encoding="utf-8", errors="replace")[
                        -5000:
                    ]
    finally:
        if controller:
            try:
                controller.stop()
            except Exception:
                pass
        if temporary:
            temporary.cleanup()
        result_path.parent.mkdir(parents=True, exist_ok=True)
        result_path.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    return 0 if result["ok"] else 1


def show_app(directory):
    import tkinter as tk
    from tkinter import messagebox, ttk

    ui_font = "PingFang SC" if sys.platform == "darwin" else "Microsoft YaHei"
    lock = AppLock(directory)
    if not lock.acquired:
        messagebox.showinfo("ScreenBridge", "软件已经运行，请打开已有的 ScreenBridge 窗口。")
        return 0
    root = tk.Tk()
    root.title("ScreenBridge")
    root.geometry("560x490" if sys.platform == "darwin" else "540x400")
    root.minsize(540, 490 if sys.platform == "darwin" else 400)
    root.configure(background="#f3f7f5")
    style = ttk.Style(root)
    style.theme_use("clam")
    style.configure("TButton", font=(ui_font, 11), padding=(14, 9))
    style.configure("TCheckbutton", background="#f3f7f5", font=(ui_font, 10))
    tk.Label(
        root,
        text="ScreenBridge",
        font=(ui_font, 26, "bold"),
        foreground="#246d51",
        background="#f3f7f5",
    ).pack(pady=(26, 2))
    tk.Label(
        root,
        text="题目复盘工作台 · 电脑与手机同步",
        font=(ui_font, 11),
        foreground="#6f8079",
        background="#f3f7f5",
    ).pack(pady=(0, 15))
    status = tk.StringVar(value="正在启动软件…")
    tk.Label(
        root,
        textvariable=status,
        font=(ui_font, 10),
        foreground="#246d51",
        wraplength=480,
        background="#f3f7f5",
    ).pack(pady=8)
    demo = tk.BooleanVar(value=False)
    ttk.Checkbutton(root, text="演示模式（不调用 AI）", variable=demo).pack(pady=5)
    controller = {"value": None, "busy": False, "closing": False}
    notifications = []
    note_lock = threading.Lock()

    def notify(value):
        with note_lock:
            notifications.append(value)

    def open_workbench():
        runtime = controller["value"]
        if runtime:
            webbrowser.open(runtime.workbench_url)

    def open_qr():
        path = directory / "phone-qr.png"
        if path.exists() and sys.platform == "win32":
            os.startfile(path)
        elif path.exists() and sys.platform == "darwin":
            subprocess.Popen(["/usr/bin/open", str(path)])
        else:
            messagebox.showinfo("手机配对", f"配对二维码文件：{path}")

    def launch():
        if controller["busy"]:
            return
        controller["busy"] = True
        requested_demo = demo.get()
        status.set("正在启动软件，请稍候…")

        def worker():
            runtime = None
            try:
                if controller["value"]:
                    controller["value"].stop()
                runtime = RuntimeController(directory, demo=requested_demo)
                runtime.start()
                controller["value"] = runtime
                notify("ready")
            except Exception as exc:
                if runtime:
                    runtime.stop()
                controller["value"] = None
                notify(str(exc))
            finally:
                controller["busy"] = False

        threading.Thread(target=worker, daemon=True).start()

    def close():
        if controller["busy"]:
            status.set("请等待启动或切换模式完成，再退出。")
            return
        if controller["closing"]:
            return
        controller["closing"] = True
        status.set("正在退出软件…")

        def worker():
            try:
                if controller["value"]:
                    controller["value"].stop()
            finally:
                notify("closed")

        threading.Thread(target=worker, daemon=True).start()

    buttons = tk.Frame(root, background="#f3f7f5")
    buttons.pack(pady=8)
    ttk.Button(buttons, text="打开工作台", command=open_workbench).grid(
        row=0, column=0, padx=5, pady=5
    )
    ttk.Button(buttons, text="手机配对二维码", command=open_qr).grid(
        row=0, column=1, padx=5, pady=5
    )
    ttk.Button(buttons, text="应用模式并重启", command=launch).grid(row=1, column=0, padx=5, pady=5)
    ttk.Button(buttons, text="退出软件", command=close).grid(row=1, column=1, padx=5, pady=5)
    if sys.platform == "darwin":
        from .macos import open_permission_settings, permission_status

        def permissions():
            try:
                open_permission_settings()
                messagebox.showinfo(
                    "Mac 截图权限",
                    "请在系统设置中为 ScreenBridge 开启屏幕录制权限；"
                    "全局快捷键及鼠标监听还需辅助功能权限，系统若询问输入监控也请授权。"
                    "完成后退出并重新打开软件。未授权时仍可导入本地题图。\n\n"
                    f"当前检测结果：{permission_status()}",
                )
            except OSError:
                messagebox.showerror("权限设置", "无法打开系统设置，请手动打开隐私与安全性。")

        ttk.Button(buttons, text="Mac 截图权限设置", command=permissions).grid(
            row=2, column=0, columnspan=2, pady=5
        )
    tk.Label(
        root,
        text="首次使用：在工作台填写模型 Key。\n手机与电脑连接同一 Wi-Fi，扫描配对二维码。"
        + (
            "\nMac：Option+S 全屏，Control+Option+A 选区。\n首次截图前请完成权限设置并重启软件。"
            if sys.platform == "darwin"
            else ""
        ),
        font=(ui_font, 9),
        foreground="#6f8079",
        background="#f3f7f5",
    ).pack(pady=8)

    def pump():
        with note_lock:
            pending, notifications[:] = list(notifications), []
        for value in pending:
            if value == "ready":
                status.set(
                    "已启动，截图客户端已连接。"
                    + (" 当前为演示模式。" if controller["value"].demo else "")
                )
                open_workbench()
            elif value == "closed":
                root.destroy()
                return
            else:
                status.set(value)
        root.after(100, pump)

    root.protocol("WM_DELETE_WINDOW", close)
    root.after(100, pump)
    root.after(150, launch)
    try:
        root.mainloop()
    finally:
        if controller["value"]:
            controller["value"].stop()
        lock.close()
    return 0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--native", action="store_true")
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--package-smoke", action="store_true")
    parser.add_argument("--result-path", type=Path, default=Path("packaged-smoke.json"))
    args = parser.parse_args()
    directory = args.data_dir or data_directory()
    redirect_windowed_logs(directory, "desktop.log" if args.native else "app.log")
    if args.package_smoke:
        return package_smoke(args.result_path)
    if args.native:
        from .desktop import run_desktop

        token = (directory / "pair-token.txt").read_text().strip()
        run_desktop(
            f"http://127.0.0.1:{args.port}", token, session_file=directory / "desktop-session.txt"
        )
        return 0
    return show_app(directory)


if __name__ == "__main__":
    raise SystemExit(main())
