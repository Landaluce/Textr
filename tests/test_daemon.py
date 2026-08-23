import json
import re
import threading
from types import SimpleNamespace

from conftest import type_string
from pynput import keyboard


class FakeSignal:
    def __init__(self):
        self.sent = []

    def emit(self, value):
        self.sent.append(value)


class FakeUI:
    def __init__(self):
        self.daemon_command = FakeSignal()


class TestListenerSurvival:
    def test_malformed_snippet_does_not_kill_handler(self, d):
        d.add_or_update_snippet("bad1", {"value": "no type here"})
        for ch in "xbad1":
            assert d.on_press(SimpleNamespace(char=ch)) is True
        # Handler still alive: a valid trigger still expands afterwards.
        type_string(d, " sig")
        assert d.buffer == ""  # expansion consumed the buffer

    def test_internal_exception_keeps_listener_alive(self, d, monkeypatch):
        def boom(text):
            raise RuntimeError("render failure")

        monkeypatch.setattr(d, "render_template", boom)
        for ch in " sig":
            assert d.on_press(SimpleNamespace(char=ch)) is True

        # Recovery once the fault is gone.
        monkeypatch.setattr(d, "render_template", lambda text: text)
        type_string(d, " again")
        assert d.buffer.endswith("again")

    def test_concurrent_snippet_mutations(self, d):
        stop = threading.Event()
        errors = []

        def writer():
            try:
                i = 0
                while not stop.is_set():
                    d.add_or_update_snippet(f"t{i}", {"type": "variable", "value": str(i)})
                    d.replace_snippet(f"t{i}", f"u{i}", {"type": "variable", "value": str(i)})
                    d.delete_snippet(f"u{i}")
                    i += 1
            except Exception as e:  # pragma: no cover
                errors.append(e)

        t = threading.Thread(target=writer)
        t.start()
        try:
            for _ in range(500):
                assert d.on_press(SimpleNamespace(char="x")) is True
        finally:
            stop.set()
            t.join()

        assert not errors
        # Daemon still functional after the churn.
        type_string(d, " sig")
        assert d.buffer == ""


class TestMatching:
    def test_variable_expansion_and_backspaces(self, d):
        type_string(d, "sig")
        fake = d.controller
        assert len(fake.backspaces()) == len("sig")
        assert d.buffer == ""

    def test_word_boundary_required(self, d):
        type_string(d, "xsig")  # 'x' before trigger -> no boundary
        assert d.buffer != ""
        assert d.controller.backspaces() == []

    def test_buffer_resets_on_special_keys(self, d):
        type_string(d, "hi")
        d.on_press(keyboard.Key.enter)
        assert d.buffer == ""

        type_string(d, "hi")
        d.on_press(keyboard.Key.esc)
        assert d.buffer == ""

    def test_command_fires_even_when_paused(self, d):
        ui = FakeUI()
        d.ui = ui
        d.enabled = False
        type_string(d, " :d")
        assert ui.daemon_command.sent == ["insert_date"]
        assert len(d.controller.backspaces()) == len(":d")


class TestCommandRouting:
    def test_ui_actions_routed_via_signal(self, d):
        ui = FakeUI()
        d.ui = ui

        d.run_command({"action": "date"})
        d.run_command({"action": "time"})
        d.run_command({"action": "datetime"})
        d.run_command({"action": "ui"})
        d.run_command({"action": "top"})
        d.run_command({"action": "hide"})
        d.run_command({"action": "reload"})
        d.run_command({"action": "restart"})

        assert ui.daemon_command.sent == [
            "insert_date", "insert_time", "insert_datetime",
            "ui", "top", "hide", "refresh", "restart",
        ]

    def test_toggle_writing_mode_emits_sync(self, d):
        ui = FakeUI()
        d.ui = ui
        d.writing_mode = False
        d.run_command({"action": "toggle_writing_mode"})
        assert d.writing_mode is True
        assert ui.daemon_command.sent == ["sync_writing_mode"]


class TestSnippetHelpers:
    def test_add_update_replace_delete_persist(self, d):
        d.add_or_update_snippet("new1", {"type": "variable", "value": "v"})
        with open(d.snippets_file) as f:
            saved = json.load(f)
        assert saved["new1"]["value"] == "v"

        d.replace_snippet("new1", "new2", {"type": "variable", "value": "w"})
        with open(d.snippets_file) as f:
            saved = json.load(f)
        assert "new1" not in saved and saved["new2"]["value"] == "w"

        d.delete_snippet("new2")
        with open(d.snippets_file) as f:
            saved = json.load(f)
        assert "new2" not in saved

    def test_import_snippets_thread_safe_and_unsaved(self, d):
        d.import_snippets({"imp": {"type": "variable", "value": "x"}})
        assert "imp" in d.snippets
        with open(d.snippets_file) as f:
            assert "imp" not in json.load(f)

    def test_get_by_category_returns_snapshot(self, d):
        all_snippets = d.get_snippets_by_category("all")
        all_snippets["injected"] = {"type": "variable"}
        assert "injected" not in d.snippets


class TestDefensiveLookup:
    def test_missing_type_returns_none(self, d):
        d.add_or_update_snippet("notype", {"value": "x"})
        assert d.is_valid_trigger("notype") is None
        assert d.is_valid_trigger("does-not-exist") is None

    def test_template_without_languages_returns_none(self, d):
        d.add_or_update_snippet("nolangs", {"type": "template"})
        assert d.is_valid_trigger("nolangs") is None


class TestContextVars:
    def test_iso_renders_as_iso8601(self, d):
        rendered = d.render_template("{iso}")
        assert re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$", rendered)

    def test_week_renders_as_number(self, d):
        rendered = d.render_template("wk {week}")
        assert re.match(r"^wk \d{1,2}$", rendered)

    def test_unknown_var_left_untouched(self, d):
        assert d.render_template("{nope}") == "{nope}"
