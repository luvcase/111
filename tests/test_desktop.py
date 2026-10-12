import io
import sys
from types import SimpleNamespace

import pytest
from PIL import Image

from screenbridge.desktop import capture, validate_server


def test_screenshot_encodes_real_capture_buffer(monkeypatch):
    class Screen:
        monitors = [{}, {"left": 0, "top": 0, "width": 100, "height": 60}]

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def grab(self, monitor):
            assert monitor == self.monitors[1]
            return SimpleNamespace(size=(100, 60), rgb=b"\xff\x00\x00" * 6000)

    monkeypatch.setitem(sys.modules, "mss", SimpleNamespace(mss=Screen))
    image = Image.open(io.BytesIO(capture()))
    assert image.size == (100, 60)
    assert image.getpixel((10, 10))[0] > 250
    with pytest.raises(ValueError, match="显示器"):
        capture(2)


def test_desktop_server_validation():
    assert validate_server("http://127.0.0.1:8765/") == "http://127.0.0.1:8765"
    for bad in ["file:///etc/passwd", "https://user:secret@example.com", "http://a/#token=x"]:
        with pytest.raises(ValueError):
            validate_server(bad)
