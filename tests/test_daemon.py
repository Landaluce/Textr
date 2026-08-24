import json
import re
import threading
from datetime import datetime
from types import SimpleNamespace

import daemon as daemon_mod
from conftest import FakeClipboard, FakeController, type_string
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
        d.enabled = False
        type_string(d, " :d")
        # The date is typed into the active window, not routed to the UI.
        assert re.match(r"^\d{4}-\d{2}-\d{2}$", daemon_mod.pyperclip.paste())
        assert len(d.controller.backspaces()) == len(":d")


class TestCommandRouting:
    def test_ui_actions_routed_via_signal(self, d):
        ui = FakeUI()
        d.ui = ui

        d.run_command({"action": "ui"})
        d.run_command({"action": "top"})
        d.run_command({"action": "hide"})
        d.run_command({"action": "reload"})
        d.run_command({"action": "restart"})

        assert ui.daemon_command.sent == [
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

    def test_import_snippets_thread_safe_and_persisted(self, d):
        d.import_snippets({"imp": {"type": "variable", "value": "x"}})
        assert "imp" in d.snippets
        with open(d.snippets_file) as f:
            assert json.load(f)["imp"]["value"] == "x"

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


class TestTextInsertingCommands:
    def test_time_command_expands_in_active_window(self, d):
        d.add_or_update_snippet(":t", {"type": "command", "action": "time"})

        type_string(d, " :t")

        assert re.match(r"^\d{2}:\d{2}$", daemon_mod.pyperclip.paste())

    def test_datetime_command_expands_in_active_window(self, d):
        # ':dt' collides with the fixture's ':d' prefix; the longer trigger
        # must win (see TestPrefixTriggers).
        d.add_or_update_snippet(":dt", {"type": "command", "action": "datetime"})

        type_string(d, " :dt")

        assert re.match(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$", daemon_mod.pyperclip.paste())
        assert len(d.controller.backspaces()) == len(":dt")


class TestCursorPlaceholder:
    def test_cursor_moves_caret_back_after_expansion(self, d):
        d.add_or_update_snippet("cur", {"type": "variable", "value": "if {cursor} else {}"})

        type_string(d, " cur")

        # Text is pasted with the {cursor} marker removed...
        assert daemon_mod.pyperclip.paste() == "if  else {}"
        # ...and the caret is moved back by the length of the text after it.
        lefts = [
            k for action, k in d.controller.events
            if action == "press" and k == keyboard.Key.left
        ]
        assert len(lefts) == len(" else {}")

    def test_expansion_without_cursor_does_not_move_caret(self, d):
        type_string(d, " sig")
        lefts = [
            k for action, k in d.controller.events
            if action == "press" and k == keyboard.Key.left
        ]
        assert lefts == []

    def test_cursor_var_renders_literal_in_templates(self, d):
        assert d.render_template("a{cursor}b") == "a{cursor}b"


class TestPrefixTriggers:
    """A trigger that is the prefix of another is deferred until disambiguated."""

    def _add_datetime(self, d):
        d.add_or_update_snippet(":dt", {"type": "command", "action": "datetime"})

    def test_longer_trigger_wins_after_deferral(self, d):
        self._add_datetime(d)

        type_string(d, " :dt")

        assert re.match(
            r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$", daemon_mod.pyperclip.paste()
        )
        assert len(d.controller.backspaces()) == len(":dt")

    def test_prefix_fires_on_boundary_char(self, d):
        self._add_datetime(d)

        type_string(d, " :d ")

        assert re.match(r"^\d{4}-\d{2}-\d{2}$", daemon_mod.pyperclip.paste())
        assert len(d.controller.backspaces()) == len(":d")

    def test_prefix_fires_on_boundary_key(self, d):
        self._add_datetime(d)

        type_string(d, " :d")
        d.on_press(keyboard.Key.enter)

        assert re.match(r"^\d{4}-\d{2}-\d{2}$", daemon_mod.pyperclip.paste())

    def test_broken_prefix_aborts_without_expansion(self, d):
        self._add_datetime(d)
        before = len(d.controller.backspaces())

        type_string(d, " :da")  # ':da' can never become ':dt'

        assert len(d.controller.backspaces()) == before
        assert d.pending_trigger is None

    def test_unrelated_trigger_unaffected_by_prefix(self, d):
        self._add_datetime(d)
        before = len(d.controller.backspaces())

        type_string(d, " sig")

        assert len(d.controller.backspaces()) - before == len("sig")


class TestNormalizedKeys:
    """The core handler accepts backend-agnostic Key objects (from evdev)."""

    def test_key_chars_expand_like_pynput(self, d):
        from keys import Key

        for ch in " sig":
            d.on_press(Key(char=ch))
        assert len(d.controller.backspaces()) == len("sig")

    def test_backspace_by_name_pops_buffer(self, d):
        from keys import Key

        d.on_press(Key(char="x"))
        d.on_press(Key(name="backspace"))
        assert d.buffer == ""

    def test_special_key_by_name_resets_buffer(self, d):
        from keys import Key

        d.on_press(Key(char="a"))
        d.on_press(Key(name="enter"))
        assert d.buffer == ""


class TestPasteHotkey:
    def test_default_combo_parses(self, d):
        mods, key = d._paste_hotkey_parts("ctrl+shift+v")
        assert mods == [keyboard.Key.ctrl, keyboard.Key.shift]
        assert key == "v"

    def test_single_modifier(self, d):
        mods, key = d._paste_hotkey_parts("ctrl+v")
        assert mods == [keyboard.Key.ctrl]
        assert key == "v"

    def test_named_key(self, d):
        mods, key = d._paste_hotkey_parts("ctrl+shift+insert")
        assert mods == [keyboard.Key.ctrl, keyboard.Key.shift]
        assert key == keyboard.Key.insert

    def test_case_insensitive(self, d):
        mods, key = d._paste_hotkey_parts("Ctrl+Shift+V")
        assert mods == [keyboard.Key.ctrl, keyboard.Key.shift]
        assert key == "v"

    def test_empty_combo_falls_back_to_default(self, d):
        mods, key = d._paste_hotkey_parts("")
        assert mods == [keyboard.Key.ctrl, keyboard.Key.shift]
        assert key == "v"

    def test_unknown_modifier_falls_back_to_ctrl(self, d):
        mods, key = d._paste_hotkey_parts("meta+v")
        assert mods == [keyboard.Key.ctrl]
        assert key == "v"

    def test_replace_uses_configured_hotkey(self, d):
        d.config["paste_hotkey"] = "ctrl+alt+v"
        type_string(d, " sig")
        tail = d.controller.events[-6:]
        assert [action for action, _ in tail] == [
            "press", "press", "press", "release", "release", "release",
        ]
        assert [key for _, key in tail] == [
            keyboard.Key.ctrl, keyboard.Key.alt, "v",
            "v", keyboard.Key.alt, keyboard.Key.ctrl,
        ]

    def test_default_hotkey_still_ctrl_shift_v(self, d):
        type_string(d, " sig")
        tail = d.controller.events[-6:]
        assert [key for _, key in tail] == [
            keyboard.Key.ctrl, keyboard.Key.shift, "v",
            "v", keyboard.Key.shift, keyboard.Key.ctrl,
        ]


class TestDateFormatConfig:
    def test_context_uses_configured_date_format(self, d):
        d.config["date_format"] = "%d/%m/%Y"
        assert d.get_context()["date"] == datetime.now().strftime("%d/%m/%Y")

    def test_context_uses_configured_time_format(self, d):
        d.config["time_format"] = "%H:%M:%S"
        assert d.get_context()["time"] == datetime.now().strftime("%H:%M:%S")

    def test_date_command_uses_configured_format(self, d):
        d.add_or_update_snippet(":d", {"type": "command", "action": "date", "category": "test"})
        d.config["date_format"] = "%d/%m/%Y"
        type_string(d, " :d")
        assert daemon_mod.pyperclip.paste() == datetime.now().strftime("%d/%m/%Y")

    def test_datetime_command_combines_formats(self, d):
        d.add_or_update_snippet(":dt", {"type": "command", "action": "datetime", "category": "test"})
        d.config["date_format"] = "%d/%m/%Y"
        d.config["time_format"] = "%H:%M"
        type_string(d, " :dt")
        assert daemon_mod.pyperclip.paste() == datetime.now().strftime("%d/%m/%Y %H:%M")


class TestSnippetEnableDisable:
    def test_disabled_snippet_does_not_expand(self, d):
        d.add_or_update_snippet("off", {"type": "variable", "value": "secret", "enabled": False, "category": "test"})
        type_string(d, " off")
        # Not matched: the trigger stays in the buffer as plain text, and
        # nothing is pasted.
        assert d.buffer == " off"
        assert daemon_mod.pyperclip.paste() == ""

    def test_enabled_snippet_expands_normally(self, d):
        type_string(d, " sig")
        assert daemon_mod.pyperclip.paste() == "Alvaro <email>"

    def test_disabled_snippet_does_not_record_usage(self, d):
        d.add_or_update_snippet("off", {"type": "variable", "value": "x", "enabled": False, "category": "test"})
        type_string(d, " off")
        assert d.snippets["off"].get("usage", 0) == 0

    def test_disabled_longer_trigger_does_not_defer_enabled_short_one(self, d):
        # ':d' enabled, ':dt' disabled: typing ':d' then a boundary fires ':d'
        # immediately (no deferral because ':dt' is disabled).
        d.add_or_update_snippet(":d", {"type": "command", "action": "date", "category": "test"})
        d.add_or_update_snippet(":dt", {"type": "command", "action": "datetime", "enabled": False, "category": "test"})
        d.config["date_format"] = "%Y"
        type_string(d, " :d ")
        assert daemon_mod.pyperclip.paste() == datetime.now().strftime("%Y")

    def test_disabled_trigger_not_matched_while_typing_longer(self, d):
        # 'off' disabled, 'offx' enabled: typing 'offx' fully must expand 'offx'
        # and not be blocked by the disabled 'off' prefix.
        d.add_or_update_snippet("off", {"type": "variable", "value": "OLD", "enabled": False, "category": "test"})
        d.add_or_update_snippet("offx", {"type": "variable", "value": "NEW", "category": "test"})
        type_string(d, " offx ")
        assert daemon_mod.pyperclip.paste() == "NEW"

    def test_disabled_command_snippet_does_not_fire(self, d):
        d.add_or_update_snippet(":q", {"type": "command", "action": "shutdown", "enabled": False, "category": "test"})
        type_string(d, " :q")
        # Not matched (stays as plain text), no expansion, no exception.
        assert d.buffer == " :q"


class TestDataPaths:
    def test_xdg_fallback_without_legacy_file(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("TEXTR_CONFIG_DIR", raising=False)
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
        monkeypatch.setattr(daemon_mod.keyboard, "Controller", FakeController)
        monkeypatch.setattr(daemon_mod, "pyperclip", FakeClipboard())
        monkeypatch.setattr(daemon_mod.time, "sleep", lambda seconds: None)

        daemon = daemon_mod.Daemon(snippets_file="snippets.json")

        assert daemon.snippets_file == str(tmp_path / "cfg" / "textr" / "snippets.json")
        assert daemon.config_file == str(tmp_path / "cfg" / "textr" / "config.json")

    def test_snippets_persist_to_xdg_dir(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("TEXTR_CONFIG_DIR", raising=False)
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
        monkeypatch.setattr(daemon_mod.keyboard, "Controller", FakeController)
        monkeypatch.setattr(daemon_mod, "pyperclip", FakeClipboard())
        monkeypatch.setattr(daemon_mod.time, "sleep", lambda seconds: None)

        daemon = daemon_mod.Daemon(snippets_file="snippets.json")
        daemon.add_or_update_snippet("k", {"type": "variable", "value": "v"})

        path = tmp_path / "cfg" / "textr" / "snippets.json"
        assert path.exists()
        assert json.loads(path.read_text())["k"]["value"] == "v"

    def test_resolve_path_precedence(self, monkeypatch, tmp_path):
        monkeypatch.delenv("TEXTR_CONFIG_DIR", raising=False)
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
        daemon = daemon_mod.Daemon.__new__(daemon_mod.Daemon)

        assert daemon._resolve_path("/abs/x.json") == "/abs/x.json"
        assert daemon._resolve_path("x.json") == str(tmp_path / "cfg" / "textr" / "x.json")

        monkeypatch.chdir(tmp_path)
        (tmp_path / "y.json").write_text("{}")
        assert daemon._resolve_path("y.json") == "y.json"  # legacy file wins


class TestLayoutConfig:
    def test_config_defaults_to_us_layout(self, d):
        assert d.config.get("layout") == "us"

    def test_layout_survives_config_round_trip(self, d):
        d.config["layout"] = "de"
        d.save_config()

        reloaded = daemon_mod.Daemon(snippets_file="snippets.json")
        assert reloaded.config["layout"] == "de"

    def test_wayland_listener_gets_config_layout(self, d, monkeypatch):
        import sys

        d.config["layout"] = "fr"
        captured = {}

        class FakeListener:
            def __init__(self, on_key=None, on_hotkey=None, layout="us"):
                captured["layout"] = layout

            def start(self):
                return True

        monkeypatch.setitem(
            sys.modules, "input_wayland",
            SimpleNamespace(WaylandInputListener=FakeListener),
        )

        assert d._start_wayland_input() is True
        assert captured["layout"] == "fr"


class TestUsageTracking:
    def test_expansion_records_usage_and_last_used(self, d):
        type_string(d, " sig")
        assert d.snippets["sig"]["usage"] == 1
        assert re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$", d.snippets["sig"]["last_used"])

        type_string(d, " sig")
        assert d.snippets["sig"]["usage"] == 2

    def test_usage_persisted_on_save(self, d):
        type_string(d, " sig")
        d.save_snippets()
        with open(d.snippets_file) as f:
            saved = json.load(f)
        assert saved["sig"]["usage"] == 1

    def test_paused_expansion_does_not_record_usage(self, d):
        d.enabled = False
        type_string(d, " sig")
        assert d.snippets["sig"].get("usage", 0) == 0


class TestTags:
    def test_legacy_category_becomes_tag_on_load(self, tmp_path, monkeypatch):
        import daemon as daemon_mod
        from conftest import FakeClipboard, FakeController

        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("TEXTR_CONFIG_DIR", str(tmp_path))
        monkeypatch.setattr(daemon_mod.keyboard, "Controller", FakeController)
        monkeypatch.setattr(daemon_mod, "pyperclip", FakeClipboard())
        monkeypatch.setattr(daemon_mod.time, "sleep", lambda seconds: None)
        (tmp_path / "snippets.json").write_text(json.dumps({
            "a": {"type": "variable", "value": "1", "category": "python"},
            "b": {"type": "variable", "value": "2", "category": "uncategorized"},
            "c": {"type": "variable", "value": "3", "tags": ["web"]},
        }))

        daemon = daemon_mod.Daemon(snippets_file="snippets.json")
        assert daemon.snippets["a"]["tags"] == ["python"]
        assert daemon.snippets["b"]["tags"] == []
        assert daemon.snippets["c"]["tags"] == ["web"]  # explicit tags untouched

    def test_tags_persisted_on_save(self, d):
        d.add_or_update_snippet("tg", {"type": "variable", "value": "v", "tags": ["x", "y"]})
        d.save_snippets()
        with open(d.snippets_file) as f:
            saved = json.load(f)
        assert saved["tg"]["tags"] == ["x", "y"]


class TestDefaultLanguage:
    def test_language_loaded_from_config(self, d):
        d.config["language"] = "javascript"
        d.save_config()

        reloaded = daemon_mod.Daemon(snippets_file="snippets.json")
        assert reloaded.current_language == "javascript"

    def test_missing_language_defaults_to_python(self, d):
        assert d.current_language == "python"

    def test_set_language_persists_to_config(self, d):
        d.set_language("rust")
        with open(d.config_file) as f:
            saved = json.load(f)
        assert saved["language"] == "rust"

        reloaded = daemon_mod.Daemon(snippets_file="snippets.json")
        assert reloaded.current_language == "rust"

    def test_set_language_command_persists(self, d):
        d.run_command({"action": "set_language", "value": "go"})
        with open(d.config_file) as f:
            assert json.load(f)["language"] == "go"
        assert d.current_language == "go"


class TestContextVars:
    def test_iso_renders_as_iso8601(self, d):
        rendered = d.render_template("{iso}")
        assert re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$", rendered)

    def test_week_renders_as_number(self, d):
        rendered = d.render_template("wk {week}")
        assert re.match(r"^wk \d{1,2}$", rendered)

    def test_unknown_var_left_untouched(self, d):
        assert d.render_template("{nope}") == "{nope}"
