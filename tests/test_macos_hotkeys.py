import ctypes
import sys
import time

import pytest

from screenbridge import desktop
from screenbridge.macos_hotkeys import (
    KEYBOARD,
    NOT_HANDLED,
    PRESSED,
    RELEASED,
    SIGNATURE,
    HotkeyID,
    MacHotKeys,
    fourcc,
    parse_hotkey,
)


@pytest.mark.parametrize(
    "value,expected",
    [
        ("<alt>+s", (2048, 1)),
        ("<ctrl>+<alt>+a", (6144, 0)),
        ("<shift>+<cmd>+<f10>", (768, 109)),
        ("<alt>+i", (2048, 34)),
    ],
)
def test_mac_hotkeys_use_physical_ansi_positions(value, expected):
    assert parse_hotkey(value) == expected


@pytest.mark.parametrize(
    "value", ["s", "<esc>", "<alt>+ß", "<alt>+s+a", "<alt>+<alt>+s", "<ctrl>+<f21>"]
)
def test_mac_hotkeys_do_not_reserve_bare_or_invalid_keys(value):
    with pytest.raises(ValueError):
        parse_hotkey(value)


class FakeAPI:
    def __init__(self, fail=False):
        self.fail = fail
        self.keys = []
        self.released = []
        self.removed = []

    def install(self, callback):
        self.callback = callback
        return "handler"

    def register(self, identifier, flags, key):
        self.keys.append((identifier, flags, key))
        return (-9878 if self.fail else 0), identifier

    def decode(self, event):
        return event

    def unregister(self, reference):
        self.released.append(reference)

    def remove(self, reference):
        self.removed.append(reference)


def test_mac_os_events_route_capture_once_per_press_and_cleanup():
    api = FakeAPI()
    commands = []
    bindings = desktop.shortcut_bindings(
        "<alt>+s", "<ctrl>+<alt>+a", commands.append, lambda **values: None
    )
    listener = MacHotKeys(bindings, lambda: api)
    listener.start()
    ids = [entry[0] for entry in api.keys]
    assert [(flags, key) for identifier, flags, key in api.keys] == [(2048, 1), (6144, 0)]
    assert api.callback((123, ids[0], PRESSED)) == NOT_HANDLED
    assert api.callback((SIGNATURE, 999999, PRESSED)) == NOT_HANDLED
    for event in [
        (ids[0], PRESSED),
        (ids[0], PRESSED),
        (ids[0], RELEASED),
        (ids[0], PRESSED),
        (ids[1], PRESSED),
    ]:
        assert api.callback((SIGNATURE, *event)) == 0
    assert commands == ["full", "full", "region"]
    listener.stop()
    listener.stop()
    assert not listener.is_alive()
    assert api.released == ids and api.removed == ["handler"]


def test_failed_mac_registration_stays_visible_without_claiming_keys():
    api = FakeAPI(fail=True)
    listener = MacHotKeys({"<alt>+s": lambda: None}, lambda: api)
    listener.start()
    assert not listener.is_alive() and "-9878" in listener.status
    listener.stop()
    assert not api.released and api.removed == ["handler"]


def test_native_mac_factory_does_not_reserve_global_escape(monkeypatch):
    from screenbridge import macos_hotkeys

    monkeypatch.setattr(sys, "platform", "darwin")
    received = {}
    monkeypatch.setattr(macos_hotkeys, "MacHotKeys", lambda bindings: received.update(bindings))
    desktop.create_key_listener(None, {"<alt>+s": "capture", "<esc>": "cancel"})
    assert received == {"<alt>+s": "capture"}


@pytest.mark.skipif(sys.platform != "darwin", reason="Requires native Carbon and Cocoa/Tk")
def test_native_mac_registration_conflict_and_release():
    import tkinter as tk

    root = tk.Tk()
    root.withdraw()
    root.update()
    bindings = {"<ctrl>+<alt>+<f19>": lambda: None}
    first, second, third = [MacHotKeys(bindings) for _ in range(3)]
    try:
        first.start()
        assert first.is_alive() and not first.errors, first.status
        second.start()
        assert not second.registered and second.errors
        second.stop()
        first.stop()
        third.start()
        assert third.is_alive() and not third.errors, third.status
    finally:
        first.stop()
        second.stop()
        third.stop()
        root.destroy()


@pytest.mark.skipif(sys.platform != "darwin", reason="Requires the real Carbon event queue and Tk")
def test_native_carbon_hotkey_events_reach_tk_without_keyboard_injection():
    import tkinter as tk

    root = tk.Tk()
    root.withdraw()
    root.update()
    calls = []
    listener = MacHotKeys({"<alt>+s": lambda: calls.append("full")})
    try:
        listener.start()
        assert listener.is_alive(), listener.status
        identifier = next(iter(listener.registered))
        library = listener.api.library
        library.CreateEvent.argtypes = [
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_double,
            ctypes.c_uint32,
            ctypes.POINTER(ctypes.c_void_p),
        ]
        library.CreateEvent.restype = ctypes.c_int32
        library.SetEventParameter.argtypes = [
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_void_p,
        ]
        library.SetEventParameter.restype = ctypes.c_int32
        library.GetMainEventQueue.restype = ctypes.c_void_p
        library.PostEventToQueue.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint16]
        library.PostEventToQueue.restype = ctypes.c_int32
        library.ReleaseEvent.argtypes = [ctypes.c_void_p]
        library.ReleaseEvent.restype = None
        event = ctypes.c_void_p()
        assert library.CreateEvent(None, KEYBOARD, PRESSED, 0.0, 0, ctypes.byref(event)) == 0
        try:
            key_id = HotkeyID(SIGNATURE, identifier)
            assert (
                library.SetEventParameter(
                    event,
                    fourcc("----"),
                    fourcc("hkid"),
                    ctypes.sizeof(key_id),
                    ctypes.byref(key_id),
                )
                == 0
            )
            assert library.PostEventToQueue(library.GetMainEventQueue(), event, 1) == 0
        finally:
            library.ReleaseEvent(event)
        deadline = time.monotonic() + 3
        while not calls and time.monotonic() < deadline:
            root.update()
            time.sleep(0.01)
        assert calls == ["full"], listener.status
    finally:
        listener.stop()
        root.destroy()
