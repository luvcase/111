"""macOS system hotkey registration. No keyboard event tap is installed."""

import ctypes
import itertools
import sys
import threading


def fourcc(value):
    return int.from_bytes(value.encode("ascii"), "big")


SIGNATURE = fourcc("SBHK")
KEYBOARD = fourcc("keyb")
PRESSED = 6
RELEASED = 9
NOT_HANDLED = -9874
EXCLUSIVE = 1
IDENTIFIERS = itertools.count(1)


class HotkeyID(ctypes.Structure):
    _fields_ = [("signature", ctypes.c_uint32), ("id", ctypes.c_uint32)]


class EventType(ctypes.Structure):
    _fields_ = [("event_class", ctypes.c_uint32), ("kind", ctypes.c_uint32)]


def parse_hotkey(value):
    modifiers = {"<alt>": 1 << 11, "<ctrl>": 1 << 12, "<shift>": 1 << 9, "<cmd>": 1 << 8}
    # Physical ANSI key positions, independent of Option-translated characters.
    letters = dict(
        zip(
            "asdfhgzxcvbqwertyopuljknm",
            [
                0,
                1,
                2,
                3,
                4,
                5,
                6,
                7,
                8,
                9,
                11,
                12,
                13,
                14,
                15,
                17,
                16,
                31,
                35,
                32,
                37,
                38,
                40,
                45,
                46,
            ],
        )
    )
    letters.update(dict(zip("1234659780", [18, 19, 20, 21, 22, 23, 25, 26, 28, 29])))
    letters["i"] = 34
    functions = [
        122,
        120,
        99,
        118,
        96,
        97,
        98,
        100,
        101,
        109,
        103,
        111,
        105,
        107,
        113,
        106,
        64,
        79,
        80,
        90,
    ]
    letters.update({f"<f{i}>": code for i, code in enumerate(functions, 1)})
    flags, keys = 0, []
    for part in value.lower().split("+"):
        if part in modifiers:
            if flags & modifiers[part]:
                raise ValueError("快捷键修饰键重复")
            flags |= modifiers[part]
        else:
            keys.append(part)
    if not flags or len(keys) != 1 or keys[0] not in letters:
        raise ValueError("Mac 截图快捷键需要修饰键和一个字母、数字或 F1–F20")
    return flags, letters[keys[0]]


class CarbonHotkeyAPI:
    callback_type = ctypes.CFUNCTYPE(
        ctypes.c_int32, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p
    )

    def __init__(self):
        if sys.platform != "darwin":
            raise RuntimeError("Mac 系统快捷键只能在 macOS 使用")
        library = self.library = ctypes.CDLL("/System/Library/Frameworks/Carbon.framework/Carbon")
        library.GetEventDispatcherTarget.argtypes = []
        library.GetEventDispatcherTarget.restype = ctypes.c_void_p
        library.InstallEventHandler.argtypes = [
            ctypes.c_void_p,
            self.callback_type,
            ctypes.c_uint32,
            ctypes.POINTER(EventType),
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_void_p),
        ]
        library.InstallEventHandler.restype = ctypes.c_int32
        library.RemoveEventHandler.argtypes = [ctypes.c_void_p]
        library.RemoveEventHandler.restype = ctypes.c_int32
        library.RegisterEventHotKey.argtypes = [
            ctypes.c_uint32,
            ctypes.c_uint32,
            HotkeyID,
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.POINTER(ctypes.c_void_p),
        ]
        library.RegisterEventHotKey.restype = ctypes.c_int32
        library.UnregisterEventHotKey.argtypes = [ctypes.c_void_p]
        library.UnregisterEventHotKey.restype = ctypes.c_int32
        library.GetEventKind.argtypes = [ctypes.c_void_p]
        library.GetEventKind.restype = ctypes.c_uint32
        library.GetEventParameter.argtypes = [
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.c_void_p,
            ctypes.c_void_p,
        ]
        library.GetEventParameter.restype = ctypes.c_int32

    def install(self, callback):
        self.callback = self.callback_type(lambda handler, event, context: callback(event))
        events = (EventType * 2)(EventType(KEYBOARD, PRESSED), EventType(KEYBOARD, RELEASED))
        reference = ctypes.c_void_p()
        error = self.library.InstallEventHandler(
            self.library.GetEventDispatcherTarget(),
            self.callback,
            2,
            events,
            None,
            ctypes.byref(reference),
        )
        if error:
            raise OSError(error, "Mac 系统快捷键处理器注册失败")
        return reference

    def register(self, identifier, flags, key):
        reference = ctypes.c_void_p()
        error = self.library.RegisterEventHotKey(
            key,
            flags,
            HotkeyID(SIGNATURE, identifier),
            self.library.GetEventDispatcherTarget(),
            EXCLUSIVE,
            ctypes.byref(reference),
        )
        return error, reference

    def decode(self, event):
        identifier = HotkeyID()
        error = self.library.GetEventParameter(
            event,
            fourcc("----"),
            fourcc("hkid"),
            None,
            ctypes.sizeof(identifier),
            None,
            ctypes.byref(identifier),
        )
        if error:
            return None
        return identifier.signature, identifier.id, self.library.GetEventKind(event)

    def unregister(self, reference):
        self.library.UnregisterEventHotKey(reference)

    def remove(self, reference):
        self.library.RemoveEventHandler(reference)


class MacHotKeys:
    def __init__(self, bindings, api_factory=CarbonHotkeyAPI):
        self.bindings = [(name, parse_hotkey(name), action) for name, action in bindings.items()]
        self.api_factory = api_factory
        self.api = self.handler = None
        self.registered = {}
        self.errors = []
        self.pressed = set()
        self.failure = ""

    @property
    def status(self):
        parts = []
        if self.is_alive():
            parts.append(
                "Mac 已注册截图快捷键：" + "、".join(v[0] for v in self.registered.values())
            )
        parts.extend(self.errors)
        if self.failure:
            parts.append(self.failure)
        return "；".join(parts) or "Mac 截图快捷键未注册"

    def start(self):
        if threading.current_thread() is not threading.main_thread():
            raise RuntimeError("Mac 系统快捷键需要在应用主线程注册")
        if self.handler is not None:
            raise RuntimeError("Mac 快捷键不能重复启动")
        self.api = self.api_factory()
        self.handler = self.api.install(self._event)
        try:
            for name, (flags, key), action in self.bindings:
                identifier = next(IDENTIFIERS)
                error, reference = self.api.register(identifier, flags, key)
                if error:
                    self.errors.append(f"{name} 注册失败（macOS {error}），可能已被其他软件占用")
                else:
                    self.registered[identifier] = (name, action, reference)
        except Exception:
            self.stop()
            raise

    def _event(self, event):
        try:
            decoded = self.api.decode(event)
            if decoded is None:
                return NOT_HANDLED
            signature, identifier, kind = decoded
            if signature != SIGNATURE or identifier not in self.registered:
                return NOT_HANDLED
            if kind == RELEASED:
                self.pressed.discard(identifier)
            elif kind == PRESSED and identifier not in self.pressed:
                self.pressed.add(identifier)
                self.registered[identifier][1]()
            return 0
        except Exception as exc:
            self.failure = "Mac 快捷键回调失败：" + str(exc)[:300]
            return NOT_HANDLED

    def is_alive(self):
        return self.handler is not None and bool(self.registered) and not self.failure

    def stop(self):
        for name, action, reference in self.registered.values():
            self.api.unregister(reference)
        self.registered.clear()
        self.pressed.clear()
        if self.handler is not None:
            self.api.remove(self.handler)
            self.handler = None
