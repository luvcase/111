"""Exercise real native command routing and HTTP upload with a simulated GUI.

No host screen or global keyboard access is used. Darwin key decoding has its
own backend tests; here we replay the production shortcut callbacks end to end.
"""

import heapq
import json
import sys
import time
from types import SimpleNamespace

import httpx
import pytest
from PIL import Image, ImageTk

from screenbridge import desktop
from screenbridge.launcher import RuntimeController


@pytest.mark.parametrize(
    "scenario",
    [
        "full",
        "escape_then_full",
        "full_over_region",
        "destroy_then_full",
        "timeout_then_full",
        "selection",
        "encode_failure_then_full",
        "panel_full",
        "panel_selection",
        "panel_capture_failure_then_full",
        "panel_escape_then_full",
    ],
)
def test_native_shortcut_to_capture_and_upload(monkeypatch, tmp_path, scenario):
    runtime = RuntimeController(tmp_path / "server", demo=True, port=0)
    runtime.start(native=False)
    keyboard_state = {}
    panels = []
    panel_class = desktop.CapturePanel

    def make_panel(*args):
        panel = panel_class(*args)
        panels.append(panel)
        return panel

    monkeypatch.setattr(desktop, "CapturePanel", make_panel)
    status_path = tmp_path / "desktop-status.json"
    source = Image.new("RGB", (100, 80), "#21a878")
    capture_calls = []

    def capture_image(monitor):
        assert panels[0].suspended, "Keypad must be withdrawn before reading the screen"
        capture_calls.append(True)
        if scenario == "panel_capture_failure_then_full" and len(capture_calls) == 1:
            raise RuntimeError("test-screen-failure")
        return source.copy(), {"left": 0, "top": 0, "width": 100, "height": 80}

    monkeypatch.setattr(desktop, "capture_image", capture_image)
    monkeypatch.setattr(
        "screenbridge.macos.permission_status",
        lambda: {"screen_recording": True, "accessibility": True},
    )
    monkeypatch.setattr(ImageTk, "PhotoImage", lambda image: image)
    if scenario == "encode_failure_then_full":
        original = desktop.encode_image
        calls = []

        def encoding(image):
            calls.append(True)
            if len(calls) == 1:
                raise ValueError("test-encode-failure")
            return original(image)

        monkeypatch.setattr(desktop, "encode_image", encoding)

    class Listener:
        def __init__(self, hotkeys=None, **kwargs):
            if hotkeys is not None:
                keyboard_state.update(hotkeys)
            self.alive = False

        def start(self):
            # Mouse-operated capture must also work if the keyboard listener stops.
            self.alive = not scenario.startswith("panel_")

        def stop(self):
            self.alive = False

        def is_alive(self):
            return self.alive

    keyboard = SimpleNamespace(GlobalHotKeys=Listener)
    monkeypatch.setitem(
        sys.modules,
        "pynput",
        SimpleNamespace(
            keyboard=keyboard,
            mouse=SimpleNamespace(Listener=Listener, Button=SimpleNamespace(right=3)),
        ),
    )

    class Widget:
        def __init__(self, parent=None, **kwargs):
            self.bindings = {}
            self.destroyed = False
            self.command = kwargs.get("command")
            self.visible = False
            if parent is not None:
                self.root = parent if isinstance(parent, Root) else parent.root

        def overrideredirect(self, value):
            self.root.window = self

        def create_image(self, *args, **kwargs):
            self.root.canvas = self

        def invoke(self):
            self.command()

        def withdraw(self):
            self.visible = False

        def deiconify(self):
            self.visible = True

        def bind(self, name, callback):
            self.bindings[name] = callback

        def destroy(self):
            if self.destroyed:
                return
            self.destroyed = True
            if "<Destroy>" in self.bindings:
                self.bindings["<Destroy>"](SimpleNamespace(widget=self))

        def create_rectangle(self, *a, **kw):
            return 1

        def __getattr__(self, name):
            return lambda *a, **kw: None

    class Root(Widget):
        def __init__(self):
            super().__init__()
            self.callbacks = []
            self.serial = 0
            self.window = self.canvas = None
            self.phase = 0
            self.result = None

        def after(self, ms, callback):
            self.serial += 1
            heapq.heappush(
                self.callbacks, (time.monotonic() + ms / 1000, self.serial, ms, callback)
            )
            return self.serial

        def after_cancel(self, serial):
            self.callbacks = [item for item in self.callbacks if item[1] != serial]
            heapq.heapify(self.callbacks)

        def mainloop(self):
            if scenario.startswith("panel_"):
                with httpx.Client(timeout=2, trust_env=False) as client:
                    deadline = time.monotonic() + 3
                    while time.monotonic() < deadline:
                        if client.get(
                            runtime.base_url + "/api/status",
                            headers={"Authorization": "Bearer " + runtime.token},
                        ).json()["desktop_connected"]:
                            break
                        time.sleep(0.01)
                runtime.open_capture_panel()
            else:
                keyboard_state["<alt>+s" if scenario == "full" else "<ctrl>+<alt>+a"]()
            deadline = time.monotonic() + 8
            with httpx.Client(timeout=2, trust_env=False) as client:
                while time.monotonic() < deadline:
                    while self.callbacks and self.callbacks[0][0] <= time.monotonic():
                        heapq.heappop(self.callbacks)[3]()
                    status = json.loads(status_path.read_text())
                    if scenario.startswith("panel_"):
                        panel = panels[0]
                        if self.phase == 0 and panel.wanted and not panel.suspended:
                            self.phase = 1
                            panel.buttons[
                                "region"
                                if scenario in {"panel_selection", "panel_escape_then_full"}
                                else "full"
                            ].invoke()
                        elif self.phase == 1 and status["last_capture"] == "正在区域选框":
                            self.phase = 2
                            if scenario == "panel_escape_then_full":
                                self.window.bindings["<Escape>"]()
                            else:
                                self.canvas.bindings["<ButtonPress-1>"](SimpleNamespace(x=10, y=10))
                                self.canvas.bindings["<ButtonRelease-1>"](
                                    SimpleNamespace(x=50, y=40)
                                )
                        elif (
                            self.phase in {1, 2}
                            and status["last_capture"] in {"截图失败", "区域选框已取消"}
                            and not panel.suspended
                        ):
                            self.phase = 3
                            panel.buttons["full"].invoke()
                    if (
                        self.phase == 0
                        and scenario != "full"
                        and status["last_capture"] == "正在区域选框"
                    ):
                        self.phase = 1
                        if scenario == "full_over_region":
                            keyboard_state["<alt>+s"]()
                        elif scenario == "escape_then_full":
                            keyboard_state["<esc>"]()
                        elif scenario == "destroy_then_full":
                            self.window.destroy()
                        elif scenario == "timeout_then_full":
                            next(item[3] for item in self.callbacks if item[2] == 60000)()
                        else:
                            self.canvas.bindings["<ButtonPress-1>"](SimpleNamespace(x=10, y=10))
                            self.canvas.bindings["<ButtonRelease-1>"](SimpleNamespace(x=50, y=40))
                    if self.phase == 1 and scenario in {
                        "escape_then_full",
                        "destroy_then_full",
                        "timeout_then_full",
                        "encode_failure_then_full",
                    }:
                        status = json.loads(status_path.read_text())
                        if status["last_capture"] in {
                            "区域选框已取消",
                            "区域选框等待超过60秒，已取消",
                            "区域截图失败",
                        }:
                            self.phase = 2
                            keyboard_state["<alt>+s"]()
                    jobs = client.get(
                        runtime.base_url + "/api/jobs",
                        headers={"Authorization": "Bearer " + runtime.token},
                    ).json()["jobs"]
                    if jobs and jobs[-1]["status"] == "done":
                        if panels[0].suspended:
                            continue
                        self.result = jobs[-1]
                        return
                    time.sleep(0.01)
            raise AssertionError(f"Capture did not complete: {status}")

    root = Root()
    monkeypatch.setitem(
        sys.modules,
        "tkinter",
        SimpleNamespace(
            Tk=lambda: root,
            Toplevel=Widget,
            Canvas=Widget,
            Label=Widget,
            Frame=Widget,
            Button=Widget,
            TclError=RuntimeError,
        ),
    )
    try:
        desktop.run_desktop(
            runtime.base_url, runtime.token, session_file=tmp_path / "desktop-session.txt"
        )
        assert root.result is not None
        expected = (40, 30) if scenario in {"selection", "panel_selection"} else (100, 80)
        assert (root.result["image_info"]["width"], root.result["image_info"]["height"]) == expected
        if scenario in {"full", "panel_full", "panel_capture_failure_then_full"}:
            assert root.window is None, "Full-screen shortcut must never open a region selector"
        else:
            assert root.window.destroyed
        status = json.loads(status_path.read_text())
        assert status["last_capture"] == "截图已发送"
        assert not status["last_error"]
        if scenario.startswith("panel_"):
            assert panels[0].window.visible
            assert status["last_input"].startswith("虚拟按键")
    finally:
        runtime.stop()
