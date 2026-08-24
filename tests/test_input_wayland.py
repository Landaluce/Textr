import os
import sys
import time
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import input_wayland as wl
from keys import Key


class TestKeyTranslation:
    def test_plain_letters_and_space(self):
        h = wl.WaylandKeyHandler()
        assert h.on_key(wl.KEY_A, 1) == Key(char="a")
        assert h.on_key(wl.KEY_SPACE, 1) == Key(char=" ")

    def test_shift_makes_uppercase_and_symbols(self):
        h = wl.WaylandKeyHandler()
        h.on_key(wl.KEY_LEFTSHIFT, 1)
        assert h.on_key(wl.KEY_A, 1) == Key(char="A")
        assert h.on_key(wl.KEY_1, 1) == Key(char="!")
        assert h.on_key(wl.KEY_SEMICOLON, 1) == Key(char=":")

    def test_shift_release_resets_state(self):
        h = wl.WaylandKeyHandler()
        h.on_key(wl.KEY_LEFTSHIFT, 1)
        h.on_key(wl.KEY_LEFTSHIFT, 0)
        assert h.on_key(wl.KEY_A, 1) == Key(char="a")

    def test_caps_lock_uppercases_letters_only(self):
        h = wl.WaylandKeyHandler()
        h.on_key(wl.KEY_CAPSLOCK, 1)
        assert h.on_key(wl.KEY_A, 1) == Key(char="A")
        assert h.on_key(wl.KEY_1, 1) == Key(char="1")
        # shift cancels caps lock (standard behaviour)
        h.on_key(wl.KEY_LEFTSHIFT, 1)
        assert h.on_key(wl.KEY_A, 1) == Key(char="a")

    def test_special_keys_map_to_names(self):
        h = wl.WaylandKeyHandler()
        assert h.on_key(wl.KEY_ENTER, 1) == Key(name="enter")
        assert h.on_key(wl.KEY_BACKSPACE, 1) == Key(name="backspace")
        assert h.on_key(wl.KEY_LEFT, 1) == Key(name="left")

    def test_ctrl_or_alt_suppresses_text(self):
        h = wl.WaylandKeyHandler()
        h.on_key(wl.KEY_LEFTCTRL, 1)
        assert h.on_key(wl.KEY_C, 1) is None
        h.on_key(wl.KEY_LEFTCTRL, 0)
        h.on_key(wl.KEY_LEFTALT, 1)
        assert h.on_key(wl.KEY_TAB, 1) is None

    def test_hotkey_detected_and_not_typed(self):
        h = wl.WaylandKeyHandler()
        h.on_key(wl.KEY_LEFTCTRL, 1)
        h.on_key(wl.KEY_LEFTSHIFT, 1)
        assert h.on_key(wl.KEY_P, 1) == wl.PAUSE_HOTKEY

    def test_release_of_non_modifier_is_ignored(self):
        h = wl.WaylandKeyHandler()
        assert h.on_key(wl.KEY_A, 0) is None

    def test_repeat_event_types_the_char(self):
        h = wl.WaylandKeyHandler()
        assert h.on_key(wl.KEY_A, 2) == Key(char="a")


class TestDeviceFiltering:
    def test_keyboard_devices_accepted(self, monkeypatch):
        monkeypatch.setattr(wl, "ecodes", SimpleNamespace(EV_KEY=1, KEY_A=30))
        caps = {1: [30, 42, 57]}
        assert wl._is_keyboard_device("AT Translated Set 2 keyboard", caps)

    def test_ydotool_virtual_device_excluded(self, monkeypatch):
        monkeypatch.setattr(wl, "ecodes", SimpleNamespace(EV_KEY=1, KEY_A=30))
        assert not wl._is_keyboard_device("ydotool virtual keyboard", {1: [30]})

    def test_mouse_excluded(self, monkeypatch):
        monkeypatch.setattr(wl, "ecodes", SimpleNamespace(EV_KEY=1, KEY_A=30))
        assert not wl._is_keyboard_device("Logitech Mouse", {2: [0, 1]})

    def test_none_name_excluded(self, monkeypatch):
        monkeypatch.setattr(wl, "ecodes", SimpleNamespace(EV_KEY=1, KEY_A=30))
        assert not wl._is_keyboard_device(None, {1: [30]})


class TestListenerDispatch:
    def test_dispatches_keys_and_hotkey(self, monkeypatch):
        fake_ecodes = SimpleNamespace(EV_KEY=1, KEY_A=30)

        class FakeEvent:
            def __init__(self, type, code, value):
                self.type = type
                self.code = code
                self.value = value

        events = [
            FakeEvent(1, wl.KEY_A, 1),   # 'a'
            FakeEvent(1, wl.KEY_LEFTCTRL, 1),
            FakeEvent(1, wl.KEY_LEFTSHIFT, 1),
            FakeEvent(1, wl.KEY_P, 1),   # pause hotkey
            FakeEvent(1, wl.KEY_A, 0),   # release -> ignored
        ]

        class FakeDevice:
            name = "fake keyboard"

            def __init__(self, path):
                pass

            def capabilities(self, absinfo=False):
                return {fake_ecodes.EV_KEY: [wl.KEY_A]}

            def read_loop(self):
                for event in events:
                    yield event

            def close(self):
                pass

        monkeypatch.setattr(wl, "ecodes", fake_ecodes)
        monkeypatch.setattr(wl, "evdev", SimpleNamespace(InputDevice=FakeDevice))
        monkeypatch.setattr(wl.glob, "glob", lambda pattern: ["/dev/input/event0"])

        received = []
        hotkeys = []
        listener = wl.WaylandInputListener(
            on_key=received.append, on_hotkey=lambda: hotkeys.append(1)
        )

        assert listener.start() is True
        for _ in range(100):
            if received or hotkeys:
                break
            time.sleep(0.01)

        assert received == [Key(char="a")]
        assert hotkeys == [1]
        listener.stop()

    def test_start_reports_failure_without_evdev(self, monkeypatch):
        monkeypatch.setattr(wl, "evdev", None)
        listener = wl.WaylandInputListener()
        assert listener.start() is False

    def test_start_reports_failure_without_devices(self, monkeypatch):
        fake_ecodes = SimpleNamespace(EV_KEY=1)
        monkeypatch.setattr(wl, "ecodes", fake_ecodes)
        monkeypatch.setattr(wl, "evdev", SimpleNamespace(InputDevice=object))
        monkeypatch.setattr(wl.glob, "glob", lambda pattern: [])
        listener = wl.WaylandInputListener()
        assert listener.start() is False
