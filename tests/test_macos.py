from pathlib import Path
from types import SimpleNamespace

import pytest

from screenbridge import macos
from screenbridge.desktop import capture_image, pixel_selection
from screenbridge.launcher import data_directory


@pytest.mark.parametrize(
    "logical,pixels,box,expected",
    [
        ((100, 60), (200, 120), (10, 5, 40, 30), (20, 10, 80, 60)),
        ((100, 60), (150, 120), (1, 1, 99, 59), (1, 2, 149, 118)),
        ((100, 60), (100, 60), (0, 0, 100, 60), (0, 0, 100, 60)),
    ],
)
def test_selection_preserves_source_pixels(logical, pixels, box, expected):
    assert pixel_selection(box, logical, pixels) == expected


def test_mac_uses_application_support(monkeypatch, tmp_path):
    monkeypatch.setattr("screenbridge.launcher.sys.platform", "darwin")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    directory = data_directory()
    assert directory == tmp_path / "Library/Application Support/ScreenBridge"
    assert directory.is_dir()


def test_permission_probe_does_not_prompt(monkeypatch):
    monkeypatch.setattr(macos.sys, "platform", "darwin")

    class Function:
        def __init__(self, result):
            self.result = result

        def __call__(self):
            return self.result

    library = SimpleNamespace(
        CGPreflightScreenCaptureAccess=Function(True), AXIsProcessTrusted=Function(False)
    )
    monkeypatch.setattr(macos.ctypes, "CDLL", lambda path: library)
    assert macos.permission_status() == {"screen_recording": True, "accessibility": False}
    assert not hasattr(library, "CGRequestScreenCaptureAccess")


def test_failed_permission_probe_stays_unknown(monkeypatch):
    monkeypatch.setattr(macos.sys, "platform", "darwin")

    def unavailable(path):
        raise OSError("not available")

    monkeypatch.setattr(macos.ctypes, "CDLL", unavailable)
    assert macos.permission_status() == {"screen_recording": None, "accessibility": None}


def test_no_capture_without_verified_screen_permission(monkeypatch):
    import sys

    monkeypatch.setitem(sys.modules, "mss", SimpleNamespace())
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(
        macos, "permission_status", lambda: {"screen_recording": False, "accessibility": False}
    )
    with pytest.raises(RuntimeError, match="屏幕录制权限"):
        capture_image()
