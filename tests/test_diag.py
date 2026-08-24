import os
from types import SimpleNamespace

import diag


class TestSessionInfo:
    def test_env_vars(self, monkeypatch):
        monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
        monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
        monkeypatch.delenv("DISPLAY", raising=False)
        info = diag.session_info()
        assert info["xdg_session_type"] == "wayland"
        assert info["wayland_display"] == "wayland-0"
        assert info["display"] == "(unset)"

    def test_unset_all(self, monkeypatch):
        monkeypatch.delenv("XDG_SESSION_TYPE", raising=False)
        monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
        monkeypatch.delenv("DISPLAY", raising=False)
        info = diag.session_info()
        assert all(v == "(unset)" for v in info.values())


class TestDeviceInfo:
    def test_keyboard_device_listed(self, monkeypatch, tmp_path):
        fake_path = tmp_path / "event3"
        fake_path.write_bytes(b"")
        os.chmod(fake_path, 0o666)

        class FakeInputDevice:
            def __init__(self, path):
                self.name = "AT Translated Set 2 keyboard"

            def capabilities(self, absinfo=False):
                return {1: [1, 30]}  # EV_KEY with KEY_A

            def close(self):
                pass

        monkeypatch.setattr(diag, "evdev", SimpleNamespace(InputDevice=FakeInputDevice))
        monkeypatch.setattr(diag.glob, "glob", lambda pat: [str(fake_path)])
        devices = diag.device_info()
        assert len(devices) == 1
        assert devices[0]["keyboard"] is True
        assert devices[0]["readable"] is True
        assert devices[0]["name"] == "AT Translated Set 2 keyboard"

    def test_ydotool_device_excluded(self, monkeypatch, tmp_path):
        fake_path = tmp_path / "event2"
        fake_path.write_bytes(b"")
        os.chmod(fake_path, 0o666)

        class FakeYdotoolDevice:
            def __init__(self, path):
                self.name = "ydotool virtual device"

            def capabilities(self, absinfo=False):
                return {1: [30]}

            def close(self):
                pass

        monkeypatch.setattr(diag, "evdev", SimpleNamespace(InputDevice=FakeYdotoolDevice))
        monkeypatch.setattr(diag.glob, "glob", lambda pat: [str(fake_path)])
        devices = diag.device_info()
        assert devices[0]["keyboard"] is False

    def test_no_evdev_returns_empty(self, monkeypatch):
        monkeypatch.setattr(diag, "evdev", None)
        assert diag.device_info() == []


class TestToolCheck:
    def test_ydotool_present(self, monkeypatch):
        monkeypatch.setattr(
            diag.shutil, "which",
            lambda name: "/usr/bin/ydotool" if name == "ydotool" else None,
        )
        tools = diag.tool_check()
        assert tools["ydotool"] == "/usr/bin/ydotool"
        assert tools["evdev"] is True
        assert "us" in tools["layouts"]

    def test_ydotool_missing(self, monkeypatch):
        monkeypatch.setattr(diag.shutil, "which", lambda name: None)
        assert diag.tool_check()["ydotool"] is None


class TestCaptureKeycodes:
    def test_yields_mapped_characters(self, monkeypatch):
        events = iter([SimpleNamespace(type=1, code=30, value=1)])  # KEY_A press

        class FakeInputDevice:
            def __init__(self, path):
                self.name = "fake keyboard"

            def capabilities(self, absinfo=False):
                return {1: [30]}

            def read_loop(self):
                return events

            def close(self):
                pass

        monkeypatch.setattr(diag, "evdev", SimpleNamespace(InputDevice=FakeInputDevice))
        monkeypatch.setattr(diag.glob, "glob", lambda pat: ["/dev/input/event0"])
        got = list(diag.capture_keycodes(duration=1))
        assert got == [(30, 1, "a")]

    def test_releases_ignored(self, monkeypatch):
        events = iter([SimpleNamespace(type=1, code=30, value=0)])

        class FakeInputDevice:
            def __init__(self, path):
                self.name = "fake keyboard"

            def capabilities(self, absinfo=False):
                return {1: [30]}

            def read_loop(self):
                return events

            def close(self):
                pass

        monkeypatch.setattr(diag, "evdev", SimpleNamespace(InputDevice=FakeInputDevice))
        monkeypatch.setattr(diag.glob, "glob", lambda pat: ["/dev/input/event0"])
        assert list(diag.capture_keycodes(duration=1)) == []


class TestRenderReport:
    def test_covers_all_sections(self):
        report = diag.render_report(diag.session_info(), [], diag.tool_check())
        assert "Session type" in report
        assert "Input devices" in report
        assert "Tools" in report
        assert "ydotool" in report

    def test_includes_key_capture_section(self, monkeypatch):
        monkeypatch.setattr(
            diag, "capture_keycodes",
            lambda duration, layout: [(30, 1, "a"), (16, 1, "q")],
        )
        report = diag.render_report(
            diag.session_info(), [], diag.tool_check(), listen=2
        )
        assert "Key capture (2s" in report
        assert "mapped='a'" in report
