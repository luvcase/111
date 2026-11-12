"""Windows registered hotkeys, routed by the OS instead of a keyboard hook."""

import ctypes
import sys
import threading

MOD_NOREPEAT = 0x4000
WM_HOTKEY = 0x0312
WM_QUIT = 0x0012


def parse_hotkey(value):
    modifiers = {"<alt>": 1, "<ctrl>": 2, "<shift>": 4, "<cmd>": 8}
    parts = value.lower().split("+")
    flags = 0
    keys = []
    for part in parts:
        if part in modifiers:
            if flags & modifiers[part]:
                raise ValueError("快捷键修饰键重复")
            flags |= modifiers[part]
        else:
            keys.append(part)
    if not flags or len(keys) != 1:
        raise ValueError("Windows 截图快捷键需要修饰键和一个字母、数字或功能键")
    key = keys[0]
    if len(key) == 1 and key.isascii() and key.isalnum():
        code = ord(key.upper())
    elif key.startswith("<f") and key.endswith(">") and key[2:-1].isdigit():
        number = int(key[2:-1])
        if not 1 <= number <= 24 or number == 12:
            raise ValueError("功能键无效；F12 为 Windows 保留键")
        code = 0x70 + number - 1
    else:
        raise ValueError("Windows 截图快捷键暂只支持字母、数字及 F1–F24（除 F12）")
    return flags | MOD_NOREPEAT, code


class Win32HotkeyAPI:
    def __init__(self):
        if sys.platform != "win32":
            raise RuntimeError("Windows 快捷键只能在 Windows 使用")
        from ctypes import wintypes

        self.types = wintypes
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self.user32.RegisterHotKey.argtypes = [
            wintypes.HWND,
            ctypes.c_int,
            wintypes.UINT,
            wintypes.UINT,
        ]
        self.user32.RegisterHotKey.restype = wintypes.BOOL
        self.user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
        self.user32.UnregisterHotKey.restype = wintypes.BOOL
        self.user32.PeekMessageW.argtypes = [
            ctypes.POINTER(wintypes.MSG),
            wintypes.HWND,
            wintypes.UINT,
            wintypes.UINT,
            wintypes.UINT,
        ]
        self.user32.PeekMessageW.restype = wintypes.BOOL
        self.user32.GetMessageW.argtypes = [
            ctypes.POINTER(wintypes.MSG),
            wintypes.HWND,
            wintypes.UINT,
            wintypes.UINT,
        ]
        self.user32.GetMessageW.restype = ctypes.c_int
        self.user32.PostThreadMessageW.argtypes = [
            wintypes.DWORD,
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPARAM,
        ]
        self.user32.PostThreadMessageW.restype = wintypes.BOOL
        self.kernel32.GetCurrentThreadId.restype = wintypes.DWORD

    def prepare(self):
        message = self.types.MSG()
        self.user32.PeekMessageW(ctypes.byref(message), None, 0, 0, 0)
        return self.kernel32.GetCurrentThreadId()

    def register(self, identifier, modifiers, key):
        if self.user32.RegisterHotKey(None, identifier, modifiers, key):
            return 0
        return ctypes.get_last_error() or 1

    def unregister(self, identifier):
        self.user32.UnregisterHotKey(None, identifier)

    def receive(self):
        message = self.types.MSG()
        result = self.user32.GetMessageW(ctypes.byref(message), None, 0, 0)
        if result == -1:
            raise OSError(ctypes.get_last_error(), "Windows 快捷键消息读取失败")
        return (message.message, int(message.wParam)) if result else (WM_QUIT, 0)

    def quit(self, thread_id):
        return self.user32.PostThreadMessageW(thread_id, WM_QUIT, 0, 0)


class WindowsHotKeys:
    def __init__(self, bindings, api_factory=Win32HotkeyAPI):
        self.bindings = [
            (name, parse_hotkey(name), callback) for name, callback in bindings.items()
        ]
        self.api_factory = api_factory
        self.ready = threading.Event()
        self.stopping = threading.Event()
        self.thread = None
        self.api = None
        self.thread_id = None
        self.registered = {}
        self.errors = []
        self.failure = ""

    @property
    def status(self):
        parts = []
        if self.registered and self.is_alive():
            names = "、".join(name for name, callback in self.registered.values())
            parts.append("Windows 已接管截图快捷键：" + names)
        if self.errors:
            parts.append("；".join(self.errors))
        if self.failure:
            parts.append(self.failure)
        return "；".join(parts) or "Windows 截图快捷键未注册"

    def start(self):
        if self.thread is not None:
            raise RuntimeError("快捷键监听不能重复启动")
        self.thread = threading.Thread(target=self._run, name="windows-hotkeys", daemon=True)
        self.thread.start()
        if not self.ready.wait(5):
            self.stop()
            raise RuntimeError("Windows 快捷键注册超时")

    def _run(self):
        owned = []
        try:
            self.api = self.api_factory()
            self.thread_id = self.api.prepare()
            for identifier, (name, (flags, key), callback) in enumerate(self.bindings, 1):
                error = self.api.register(identifier, flags, key)
                if error:
                    self.errors.append(f"{name} 注册失败（Windows {error}），可能已被其他软件占用")
                else:
                    owned.append(identifier)
                    self.registered[identifier] = (name, callback)
            self.ready.set()
            while owned and not self.stopping.is_set():
                message, identifier = self.api.receive()
                if message == WM_QUIT:
                    break
                if message == WM_HOTKEY and identifier in self.registered:
                    self.registered[identifier][1]()
        except Exception as exc:
            self.failure = "Windows 快捷键监听失败：" + str(exc)[:300]
        finally:
            for identifier in owned:
                self.api.unregister(identifier)
            self.thread_id = None
            self.ready.set()

    def is_alive(self):
        return self.thread is not None and self.thread.is_alive()

    def stop(self):
        self.stopping.set()
        if self.api and self.thread_id:
            self.api.quit(self.thread_id)
        if self.thread and self.thread is not threading.current_thread():
            self.thread.join(timeout=2)
