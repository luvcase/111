"""macOS permission checks; importing this module never prompts the user."""

import ctypes
import subprocess
import sys


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
