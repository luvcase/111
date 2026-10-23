import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from screenbridge import macos
from screenbridge.desktop import capture_image, pixel_selection
from screenbridge.launcher import data_directory


@pytest.mark.parametrize("vk,char,expected", [(1, "ß", "s"), (1, "S", "s"), (0, "å", "a")])
def test_option_characters_use_physical_shortcut_keys(vk, char, expected):
    class Base:
        def __init__(self, hotkeys):
            self.hotkeys = hotkeys

        def canonical(self, key):
            return "unchanged"

    keyboard = SimpleNamespace(GlobalHotKeys=Base, KeyCode=SimpleNamespace(from_char=lambda c: c))
    listener = macos.hotkey_listener(keyboard, {})
    assert listener.canonical(SimpleNamespace(vk=vk, char=char)) == expected
    assert listener.canonical(SimpleNamespace(vk=58)) == "unchanged"


@pytest.mark.skipif(sys.platform != "darwin", reason="Exercises the actual pynput Darwin backend")
@pytest.mark.parametrize("region,right_option", [(False, False), (False, True), (True, False)])
def test_darwin_hotkey_engine_matches_option_translated_events(region, right_option):
    from pynput import keyboard

    triggered = []
    combo = "<ctrl>+<alt>+a" if region else "<alt>+s"
    listener = macos.hotkey_listener(keyboard, {combo: lambda: triggered.append(True)})
    modifiers = [keyboard.Key.ctrl_l] if region else []
    modifiers += [keyboard.Key.alt_r if right_option else keyboard.Key.alt_l]
    key = keyboard.KeyCode.from_char("å" if region else "ß", vk=0 if region else 1)
    for modifier in modifiers:
        listener._on_press(modifier, False)
    listener._on_press(key, False)
    listener._on_press(key, False)  # key auto-repeat must not submit a second capture
    assert triggered == [True]
    listener._on_release(key, False)
    for modifier in reversed(modifiers):
        listener._on_release(modifier, False)
    for modifier in modifiers:
        listener._on_press(modifier, False)
    listener._on_press(key, False)
    assert triggered == [True, True]


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
