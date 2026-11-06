import os
import sys

import pytest

from screenbridge.capture_panel import CapturePanel


@pytest.mark.skipif(
    sys.platform.startswith("linux") and not os.environ.get("DISPLAY"),
    reason="Real Tk window test requires a display (runs on Windows/macOS CI)",
)
def test_real_keypad_buttons_and_window_lifecycle():
    import tkinter as tk

    root = tk.Tk()
    root.withdraw()
    submitted = []
    panel = CapturePanel(root, lambda kind, **kwargs: submitted.append((kind, kwargs)))
    try:
        panel.show()
        root.update()
        assert panel.window.winfo_viewable()
        panel.buttons["full"].invoke()
        panel.buttons["region"].invoke()
        assert submitted == [("full", {"source": "panel"}), ("region", {"source": "panel"})]
        panel.suspend()
        root.update()
        assert not panel.window.winfo_viewable()
        panel.show()
        root.update()
        assert not panel.window.winfo_viewable()
        panel.resume()
        root.update()
        assert panel.window.winfo_viewable()
        panel.suspend()
        panel.close()
        panel.resume()
        root.update()
        assert not panel.window.winfo_viewable()
        panel.show()
        root.update()
        assert panel.window.winfo_viewable()
    finally:
        root.destroy()
