"""Windows CI: native OS key routing -> frozen capture client -> demo upload.

Uses an isolated Chrome page and SendInput through the Windows input queue.
Browser-generated key events would bypass RegisterHotKey and cannot test this.
"""

import argparse
import ctypes
import json
import tempfile
import time
from pathlib import Path

import httpx
from playwright.sync_api import sync_playwright

from screenbridge import launcher


class WindowsInput:
    def __init__(self):
        from ctypes import wintypes as wt

        class Mouse(ctypes.Structure):
            _fields_ = [
                ("dx", wt.LONG),
                ("dy", wt.LONG),
                ("mouseData", wt.DWORD),
                ("dwFlags", wt.DWORD),
                ("time", wt.DWORD),
                ("extra", ctypes.c_size_t),
            ]

        class Keyboard(ctypes.Structure):
            _fields_ = [
                ("vk", wt.WORD),
                ("scan", wt.WORD),
                ("flags", wt.DWORD),
                ("time", wt.DWORD),
                ("extra", ctypes.c_size_t),
            ]

        class Hardware(ctypes.Structure):
            _fields_ = [("msg", wt.DWORD), ("paramL", wt.WORD), ("paramH", wt.WORD)]

        class Payload(ctypes.Union):
            _fields_ = [("mouse", Mouse), ("keyboard", Keyboard), ("hardware", Hardware)]

        class Input(ctypes.Structure):
            _fields_ = [("type", wt.DWORD), ("payload", Payload)]

        self.Input = Input
        self.wt = wt
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.user32.SendInput.argtypes = [wt.UINT, ctypes.POINTER(Input), ctypes.c_int]
        self.user32.SendInput.restype = wt.UINT
        self.user32.MapVirtualKeyW.argtypes = [wt.UINT, wt.UINT]
        self.user32.MapVirtualKeyW.restype = wt.UINT
        self.user32.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
        self.user32.GetWindowTextW.restype = ctypes.c_int
        self.user32.SetForegroundWindow.argtypes = [wt.HWND]
        self.user32.SetForegroundWindow.restype = wt.BOOL
        self.user32.ShowWindow.argtypes = [wt.HWND, ctypes.c_int]
        self.user32.GetForegroundWindow.restype = wt.HWND

    def chord(self, *keys):
        sequence = [(key, False) for key in keys] + [(key, True) for key in reversed(keys)]
        inputs = (self.Input * len(sequence))()
        for item, (key, release) in zip(inputs, sequence):
            item.type = 1
            # Scan codes give Chrome meaningful event.code values, like physical keys.
            item.payload.keyboard.scan = self.user32.MapVirtualKeyW(key, 0)
            if not item.payload.keyboard.scan:
                raise RuntimeError(f"No scan code for test key {key}")
            item.payload.keyboard.flags = 8 | (2 if release else 0)
        sent = self.user32.SendInput(len(inputs), inputs, ctypes.sizeof(self.Input))
        if sent != len(inputs):
            raise RuntimeError(f"SendInput failed: {sent}/{len(inputs)}; {ctypes.get_last_error()}")

    def focus(self, title):
        handles = []
        callback_type = ctypes.WINFUNCTYPE(self.wt.BOOL, self.wt.HWND, self.wt.LPARAM)

        @callback_type
        def inspect(hwnd, parameter):
            text = ctypes.create_unicode_buffer(1024)
            self.user32.GetWindowTextW(hwnd, text, len(text))
            if title in text.value:
                handles.append(hwnd)
            return True

        self.user32.EnumWindows.argtypes = [callback_type, self.wt.LPARAM]
        self.user32.EnumWindows(inspect, 0)
        if not handles:
            raise RuntimeError("Isolated Chrome window was not found")
        self.user32.ShowWindow(handles[0], 9)
        self.user32.SetForegroundWindow(handles[0])
        if self.user32.GetForegroundWindow() != handles[0]:
            raise RuntimeError("Chrome could not become the foreground window")


def wait_for(page, condition, label):
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        value = condition()
        if value:
            return value
        page.wait_for_timeout(100)
    raise RuntimeError("Timed out: " + label)


def verify(executable, output):
    report = {"ok": False, "stage": "starting"}
    runtime = None
    directory = None
    temporary = None
    try:
        inputs = WindowsInput()
        temporary = tempfile.TemporaryDirectory(prefix="screenbridge-chrome-")
        directory = Path(temporary.name)
        # Start the actual frozen native child against the same-version demo backend.
        launcher.child_command = lambda path, port: [
            str(executable),
            "--native",
            "--data-dir",
            str(path),
            "--port",
            str(port),
        ]
        runtime = launcher.RuntimeController(directory, demo=True, port=0)
        runtime.start(native=True)
        headers = {"Authorization": "Bearer " + runtime.token}

        def diagnostics():
            try:
                return json.loads((directory / "desktop-status.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return {}

        report["diagnostics"] = diagnostics()
        assert "Windows 已接管" in diagnostics().get("hotkeys", ""), diagnostics()
        with (
            sync_playwright() as playwright,
            httpx.Client(timeout=5, trust_env=False) as client,
        ):
            browser = playwright.chromium.launch(channel="chrome", headless=False)
            report["chrome_version"] = browser.version
            page = browser.new_page()
            title = "ScreenBridge Chrome Routing Check"
            page.set_content(
                "<title>" + title + '</title><input id="typing" autofocus>'
                '<script>window.keys=[]; for (const type of ["keydown","keyup"]) {'
                "document.addEventListener(type,e=>keys.push({type:e.type,code:e.code,key:e.key,"
                "alt:e.altKey,ctrl:e.ctrlKey}));}</script>"
            )

            def focus():
                page.bring_to_front()
                inputs.focus(title)
                page.locator("#typing").click()

            def jobs():
                response = client.get(runtime.base_url + "/api/jobs", headers=headers)
                response.raise_for_status()
                return response.json()["jobs"]

            def received(code):
                return page.evaluate("code=>keys.some(e=>e.type==='keydown'&&e.code===code)", code)

            focus()
            inputs.chord(0x42)  # Ordinary B must still reach the input.
            wait_for(page, lambda: page.locator("#typing").input_value() == "b", "normal typing")
            assert received("KeyB"), "Native test input must produce real Chrome key codes"
            report["baseline_browser_events"] = page.evaluate("keys")
            report["typing_works"] = True
            report["stage"] = "full_hotkey"
            page.evaluate("keys=[]")
            before = {job["id"] for job in jobs()}
            inputs.chord(0x12, 0x53)  # Alt+S through the OS, not browser event synthesis.
            wait_for(
                page,
                lambda: any(job["id"] not in before and job["status"] == "done" for job in jobs()),
                "frozen full-screen hotkey and demo upload",
            )
            report["full_browser_events"] = page.evaluate("keys")
            assert not received("KeyS"), "Chrome received the screenshot KeyS keydown"
            report["full_capture_via_hotkey"] = True

            report["stage"] = "region_hotkey"
            focus()
            page.evaluate("keys=[]")
            inputs.chord(0x11, 0x12, 0x41)  # Ctrl+Alt+A
            wait_for(
                page,
                lambda: diagnostics().get("last_capture") == "正在区域选框",
                "region selector",
            )
            report["region_browser_events"] = page.evaluate("keys")
            assert not received("KeyA"), "Chrome received the screenshot KeyA keydown"
            inputs.chord(0x1B)  # Escape is local to the focused region selector.
            wait_for(
                page,
                lambda: diagnostics().get("last_capture") == "区域选框已取消",
                "region cancel",
            )
            report["region_selector_via_hotkey"] = True

            report["stage"] = "released_keys"
            runtime.stop()
            runtime = None
            focus()
            page.evaluate("keys=[]")
            inputs.chord(0x12, 0x53)
            wait_for(page, lambda: received("KeyS"), "hotkey returned to Chrome after app exit")
            report.update(ok=True, stage="complete", keys_restored_on_exit=True)
            browser.close()
    except Exception as exc:
        report["error"] = str(exc)
    finally:
        if runtime:
            try:
                report["diagnostics"] = json.loads(
                    (directory / "desktop-status.json").read_text(encoding="utf-8")
                )
            except (OSError, ValueError):
                pass
            for name in ("desktop.log", "native-startup-error.log"):
                path = directory / name
                if path.exists():
                    report[name] = path.read_text(encoding="utf-8", errors="replace")[-4000:]
            try:
                runtime.stop()
            except Exception as exc:
                report.update(ok=False, cleanup_error=str(exc))
        if temporary:
            try:
                temporary.cleanup()
            except OSError as exc:
                report.update(ok=False, cleanup_error=str(exc))
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(report))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--executable", type=Path, required=True)
    parser.add_argument("--result-path", type=Path, required=True)
    args = parser.parse_args()
    raise SystemExit(verify(args.executable.resolve(), args.result_path))
