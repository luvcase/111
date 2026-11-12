import queue
import sys
import threading

import pytest

from screenbridge.desktop import create_key_listener
from screenbridge.windows import MOD_NOREPEAT, WM_HOTKEY, WM_QUIT, WindowsHotKeys, parse_hotkey


@pytest.mark.parametrize(
    "value,expected",
    [
        ("<alt>+s", (MOD_NOREPEAT | 1, 83)),
        ("<ctrl>+<alt>+a", (MOD_NOREPEAT | 3, 65)),
        ("<shift>+<cmd>+<f10>", (MOD_NOREPEAT | 12, 0x79)),
    ],
)
def test_registered_hotkey_modifiers(value, expected):
    assert parse_hotkey(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        "s",
        "<esc>",
        "<alt>+s+a",
        "<alt>+<alt>+s",
        "<alt>+ß",
        "<ctrl>+<f12>",
        "<ctrl>+<f25>",
        "<alt>+",
        "<ctrl>+<space>",
    ],
)
def test_invalid_or_unmodified_keys_are_not_globally_reserved(value):
    with pytest.raises(ValueError):
        parse_hotkey(value)


class FakeAPI:
    def __init__(self, errors=None):
        self.messages = queue.Queue()
        self.errors = errors or {}
        self.calls = []
        self.released = []

    def prepare(self):
        return 42

    def register(self, identifier, flags, key):
        self.calls.append((identifier, flags, key))
        return self.errors.get(identifier, 0)

    def unregister(self, identifier):
        self.released.append(identifier)

    def receive(self):
        return self.messages.get(timeout=3)

    def quit(self, thread_id):
        assert thread_id == 42
        self.messages.put((WM_QUIT, 0))


def test_os_messages_route_once_and_release_only_owned_keys():
    api = FakeAPI(errors={2: 1409})
    called = threading.Event()
    listener = WindowsHotKeys({"<alt>+s": called.set, "<ctrl>+<alt>+a": lambda: None}, lambda: api)
    try:
        listener.start()
        assert listener.is_alive()
        assert "1409" in listener.status
        assert api.calls == [(1, MOD_NOREPEAT | 1, 83), (2, MOD_NOREPEAT | 3, 65)]
        api.messages.put((WM_HOTKEY, 999))
        api.messages.put((WM_HOTKEY, 1))
        assert called.wait(1)
    finally:
        listener.stop()
    assert not listener.is_alive()
    assert api.released == [1]


def test_failed_registration_does_not_claim_exclusive_keys():
    api = FakeAPI(errors={1: 1409})
    listener = WindowsHotKeys({"<alt>+s": lambda: None}, lambda: api)
    listener.start()
    listener.stop()
    assert not listener.registered and not api.released
    assert "注册失败" in listener.status


def test_windows_selection_keeps_escape_available_to_other_apps(monkeypatch):
    from screenbridge import windows

    monkeypatch.setattr(sys, "platform", "win32")
    received = {}
    monkeypatch.setattr(windows, "WindowsHotKeys", lambda bindings: received.update(bindings))
    create_key_listener(None, {"<alt>+s": "capture", "<esc>": "cancel"})
    assert received == {"<alt>+s": "capture"}


@pytest.mark.skipif(sys.platform != "win32", reason="Requires actual Windows hotkey registration")
def test_windows_registration_conflict_and_release():
    bindings = {"<alt>+<shift>+<f10>": lambda: None}
    first = WindowsHotKeys(bindings)
    second = WindowsHotKeys(bindings)
    third = WindowsHotKeys(bindings)
    try:
        first.start()
        assert first.registered and not first.errors, first.status
        second.start()
        assert not second.registered and second.errors
        second.stop()
        first.stop()
        third.start()
        assert third.registered and not third.errors, third.status
    finally:
        first.stop()
        second.stop()
        third.stop()
