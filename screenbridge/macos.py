"""macOS permission checks; importing this module never prompts the user."""

import ctypes
import subprocess
import sys


def hotkey_listener(keyboard, hotkeys):
    """Option/IME can change characters; keep default S/A shortcuts physical."""

    class PhysicalHotKeys(keyboard.GlobalHotKeys):
        def canonical(self, key):
            vk = getattr(key, "vk", None)
            if vk in {0x00, 0x01}:
                return keyboard.KeyCode.from_char({0x00: "a", 0x01: "s"}[vk])
            return super().canonical(key)

    return PhysicalHotKeys(hotkeys)


def permission_status():
    if sys.platform != "darwin":
        return {"screen_recording": None, "accessibility": None}
    status = {"screen_recording": None, "accessibility": None}
    libraries = {
        "screen_recording": (
            "/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics",
            "CGPreflightScreenCaptureAccess",
        ),
        "accessibility": (
            "/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices",
            "AXIsProcessTrusted",
        ),
    }
    for key, (path, symbol) in libraries.items():
        try:
            function = getattr(ctypes.CDLL(path), symbol)
            function.argtypes = []
            function.restype = ctypes.c_bool
            status[key] = bool(function())
        except (OSError, AttributeError):
            pass
    return status


def open_permission_settings():
    """Only called from the visible permission button, never during startup/CI."""
    if sys.platform != "darwin":
        return
    try:
        library = ctypes.CDLL("/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics")
        request = library.CGRequestScreenCaptureAccess
        request.argtypes = []
        request.restype = ctypes.c_bool
        request()
    except (OSError, AttributeError):
        pass
    subprocess.Popen(
        [
            "/usr/bin/open",
            "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture",
        ]
    )
