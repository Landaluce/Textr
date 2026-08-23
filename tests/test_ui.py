import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from ui import TexterUI


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def window(qapp, d):
    w = TexterUI(daemon=d)
    yield w
    w.status_timer.stop()
    w.tray_icon.hide()


class TestTrayToggle:
    def test_flips_state_on_every_click(self, window, d):
        assert d.enabled is True
        window.toggle_pause()
        assert d.enabled is False
        window.toggle_pause()
        assert d.enabled is True

    def test_resumes_after_external_pause(self, window, d):
        # Paused via Ctrl+Shift+P (daemon state flipped outside the UI).
        d.enabled = False
        window.toggle_pause()
        assert d.enabled is True


class TestStateSync:
    def test_buttons_carry_qss_hook_names(self, window):
        assert window.toggle_daemon_btn.objectName() == "toggle_daemon_btn"
        assert window.toggle_writing_btn.objectName() == "toggle_writing_btn"

    def test_poll_syncs_stale_button_tray_and_label(self, window, d):
        d.enabled = False  # changed outside the UI, e.g. hotkey
        assert window.toggle_daemon_btn.isChecked() is True  # stale

        window.poll_daemon_status()

        assert window.toggle_daemon_btn.isChecked() is False
        assert window.last_enabled_state is False
        assert "paused" in window.status_label.text()
        assert window.tray_icon.toolTip() == "Texter - Paused"

    def test_button_toggle_round_trip(self, window, d):
        window.toggle_daemon(False)
        assert d.enabled is False
        assert window.toggle_daemon_btn.isChecked() is False
        assert "paused" in window.status_label.text()

        window.toggle_daemon(True)
        assert d.enabled is True
        assert window.toggle_daemon_btn.isChecked() is True

    def test_expansion_works_after_resume_cycle(self, window, d):
        from types import SimpleNamespace

        def type_text(text):
            for ch in text:
                assert d.on_press(SimpleNamespace(char=ch)) is True

        type_text(" sig")  # baseline expansion
        window.set_daemon_enabled(False)
        type_text(" sig")  # ignored while paused
        assert d.buffer == ""

        before = len(d.controller.backspaces())
        window.toggle_pause()  # resume
        assert d.enabled is True
        type_text(" sig")
        assert len(d.controller.backspaces()) - before == len("sig")
