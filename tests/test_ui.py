import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QMessageBox, QFileDialog

from ui import AddSnippetDialog, TexterUI


def collect_triggers(tree):
    """All snippet triggers in the tree, in display order."""
    out = []

    def walk(item):
        trigger = item.data(0, Qt.UserRole)
        if trigger:
            out.append(trigger)
        for i in range(item.childCount()):
            walk(item.child(i))

    for i in range(tree.topLevelItemCount()):
        walk(tree.topLevelItem(i))
    return out


def top_level_groups(tree):
    """Names of the tree's top-level group nodes, in display order."""
    out = []
    for i in range(tree.topLevelItemCount()):
        name = tree.topLevelItem(i).data(0, Qt.UserRole + 1)
        if name:
            out.append(name)
    return out


def find_snippet_item(tree, trigger):
    """The tree item for a given snippet trigger, or None."""
    def walk(item):
        if item.data(0, Qt.UserRole) == trigger:
            return item
        for i in range(item.childCount()):
            found = walk(item.child(i))
            if found:
                return found
        return None

    for i in range(tree.topLevelItemCount()):
        found = walk(tree.topLevelItem(i))
        if found:
            return found
    return None


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


class TestLayoutSetting:
    def test_layout_combo_defaults_to_config(self, window, d):
        assert window.layout_combo.currentData() == "us"

    def test_save_settings_persists_layout(self, window, d):
        window.layout_combo.setCurrentIndex(window.layout_combo.findData("de"))
        window.save_user_config()
        assert d.config["layout"] == "de"


class TestSnippetGrouping:
    def test_default_grouping_is_by_category(self, window):
        assert window.group_combo.currentData() == "category"
        assert top_level_groups(window.snippet_list) == ["test"]
        assert collect_triggers(window.snippet_list) == [":d", "longtrig", "sig"]  # name-sorted

    def test_flat_mode_shows_triggers_only(self, window):
        window.group_combo.setCurrentIndex(window.group_combo.findData("none"))
        assert top_level_groups(window.snippet_list) == []
        assert sorted(collect_triggers(window.snippet_list)) == [":d", "longtrig", "sig"]

    def test_group_by_type(self, window):
        window.group_combo.setCurrentIndex(window.group_combo.findData("type"))
        assert top_level_groups(window.snippet_list) == ["command", "template", "variable"]

    def test_nested_category_folders(self, window, d):
        d.add_or_update_snippet("nest", {
            "type": "variable", "value": "v", "category": "python/web"
        })
        window.refresh_snippet_list()

        assert top_level_groups(window.snippet_list) == ["python", "test"]
        python_group = window.snippet_list.topLevelItem(0)
        assert python_group.child(0).data(0, Qt.UserRole + 1) == "web"
        assert collect_triggers(window.snippet_list) == ["nest", ":d", "longtrig", "sig"]

    def test_group_by_tag_lists_snippet_under_each_tag(self, window, d):
        d.add_or_update_snippet("multi", {
            "type": "variable", "value": "v", "category": "test", "tags": ["python", "web"]
        })
        window.refresh_snippet_list()
        window.group_combo.setCurrentIndex(window.group_combo.findData("tag"))

        assert top_level_groups(window.snippet_list) == ["python", "test", "web"]
        assert collect_triggers(window.snippet_list).count("multi") == 2


class TestSubGrouping:
    def test_default_is_no_subgroup(self, window):
        assert window.subgroup_combo.currentData() == "none"
        # snippets are direct children of the category group (single level)
        group = window.snippet_list.topLevelItem(0)
        assert group.data(0, Qt.UserRole + 1) == "test"
        assert group.child(0).data(0, Qt.UserRole) == ":d"

    def test_category_then_type(self, window, d):
        d.add_or_update_snippet("ff", {"type": "variable", "value": "v", "category": "other"})
        window.refresh_snippet_list()
        window.subgroup_combo.setCurrentIndex(window.subgroup_combo.findData("type"))

        assert top_level_groups(window.snippet_list) == ["other", "test"]
        # degenerate sub-groups still render (one child)
        other = window.snippet_list.topLevelItem(0)
        assert other.child(0).data(0, Qt.UserRole + 1) == "variable"
        assert other.child(0).child(0).data(0, Qt.UserRole) == "ff"
        # test category splits into command / template / variable
        test = window.snippet_list.topLevelItem(1)
        assert [test.child(i).data(0, Qt.UserRole + 1) for i in range(test.childCount())] == [
            "command", "template", "variable",
        ]

    def test_type_then_category(self, window):
        window.group_combo.setCurrentIndex(window.group_combo.findData("type"))
        window.subgroup_combo.setCurrentIndex(window.subgroup_combo.findData("category"))

        assert top_level_groups(window.snippet_list) == ["command", "template", "variable"]
        cmd = window.snippet_list.topLevelItem(0)
        assert cmd.child(0).data(0, Qt.UserRole + 1) == "test"
        assert cmd.child(0).child(0).data(0, Qt.UserRole) == ":d"

    def test_tag_then_type_duplicates_across_tags(self, window, d):
        d.add_or_update_snippet("multi", {"type": "variable", "value": "v", "tags": ["a", "b"]})
        window.refresh_snippet_list()
        window.group_combo.setCurrentIndex(window.group_combo.findData("tag"))
        window.subgroup_combo.setCurrentIndex(window.subgroup_combo.findData("type"))

        assert collect_triggers(window.snippet_list).count("multi") == 2

    def test_same_primary_and_subgroup_does_not_duplicate(self, window):
        window.subgroup_combo.setCurrentIndex(window.subgroup_combo.findData("category"))
        group = window.snippet_list.topLevelItem(0)
        assert group.data(0, Qt.UserRole + 1) == "test"
        assert group.child(0).data(0, Qt.UserRole) == ":d"

    def test_flat_mode_ignores_subgroup(self, window):
        window.group_combo.setCurrentIndex(window.group_combo.findData("none"))
        window.subgroup_combo.setCurrentIndex(window.subgroup_combo.findData("type"))
        assert top_level_groups(window.snippet_list) == []
        assert sorted(collect_triggers(window.snippet_list)) == [":d", "longtrig", "sig"]


class TestSearchAndSort:
    def test_search_filters_by_trigger(self, window):
        window.search_input.setText("sig")
        assert collect_triggers(window.snippet_list) == ["sig"]

    def test_search_filters_by_expansion_text(self, window, d):
        d.add_or_update_snippet("addr", {"type": "variable", "value": "123 Main Street"})
        window.refresh_snippet_list()
        window.search_input.setText("main street")
        assert collect_triggers(window.snippet_list) == ["addr"]

    def test_sort_by_most_used(self, window, d):
        d.add_or_update_snippet("zz", {"type": "variable", "value": "z", "usage": 5})
        d.add_or_update_snippet("aa", {"type": "variable", "value": "a", "usage": 1})
        window.refresh_snippet_list()
        window.group_combo.setCurrentIndex(window.group_combo.findData("none"))
        window.sort_combo.setCurrentIndex(window.sort_combo.findData("used"))

        triggers = collect_triggers(window.snippet_list)
        assert triggers[0] == "zz"
        assert triggers.index("aa") < triggers.index("sig")

    def test_sort_by_recent_usage(self, window, d):
        d.add_or_update_snippet("old", {"type": "variable", "value": "o", "last_used": "2020-01-01T00:00:00"})
        d.add_or_update_snippet("new", {"type": "variable", "value": "n", "last_used": "2026-01-01T00:00:00"})
        window.refresh_snippet_list()
        window.group_combo.setCurrentIndex(window.group_combo.findData("none"))
        window.sort_combo.setCurrentIndex(window.sort_combo.findData("recent"))

        triggers = collect_triggers(window.snippet_list)
        assert triggers[0] == "new"
        assert triggers.index("old") < triggers.index("sig")  # never-used sorts last


class TestDialogTags:
    def test_tags_parsed_from_comma_separated_input(self, qapp):
        dialog = AddSnippetDialog()
        dialog.trigger_input.setText("tg")
        dialog.type_combo.setCurrentText("variable")
        dialog.value_input.setPlainText("v")
        dialog.tags_input.setText("python, web, python")

        snippet = dialog.get_snippet()
        assert snippet["tags"] == ["python", "web", "python"]

    def test_empty_tags_omitted(self, qapp):
        dialog = AddSnippetDialog()
        dialog.trigger_input.setText("tg")
        dialog.type_combo.setCurrentText("variable")
        dialog.value_input.setPlainText("v")
        dialog.tags_input.setText("  ,,  ")

        snippet = dialog.get_snippet()
        assert "tags" not in snippet

    def test_edit_populates_tags_field(self, qapp):
        dialog = AddSnippetDialog(snippet={
            "trigger": "tg", "type": "variable", "value": "v", "tags": ["a", "b"]
        })
        assert dialog.tags_input.text() == "a, b"


class TestReadability:
    def test_group_headers_bold_lavender(self, window):
        group = window.snippet_list.topLevelItem(0)
        assert group.font(0).bold()
        assert group.foreground(0).color().name() == "#cba6f7"

    def test_variable_snippet_uses_light_green(self, window):
        sig_item = find_snippet_item(window.snippet_list, "sig")
        assert sig_item.foreground(0).color().name() == "#a6e3a1"

    def test_command_snippet_uses_light_red(self, window):
        d_cmd = find_snippet_item(window.snippet_list, ":d")
        assert d_cmd.foreground(0).color().name() == "#f38ba8"

    def test_template_snippet_uses_light_blue(self, window):
        long_item = find_snippet_item(window.snippet_list, "longtrig")
        assert long_item.foreground(0).color().name() == "#89b4fa"


class TestToggleButtonText:
    def test_daemon_label_tracks_state(self, window, d):
        assert window.toggle_daemon_btn.text() == "Daemon: On"
        window.set_daemon_enabled(False)
        assert window.toggle_daemon_btn.text() == "Daemon: Off"
        window.set_daemon_enabled(True)
        assert window.toggle_daemon_btn.text() == "Daemon: On"

    def test_writing_label_tracks_state(self, window):
        assert window.toggle_writing_btn.text() == "Human Typing: Off"
        window.toggle_writing_mode(True)
        assert window.toggle_writing_btn.text() == "Human Typing: On"
        window.toggle_writing_mode(False)
        assert window.toggle_writing_btn.text() == "Human Typing: Off"

    def test_sync_command_updates_writing_label(self, window, d):
        d.writing_mode = True
        window.handle_daemon_command("sync_writing_mode")
        assert window.toggle_writing_btn.text() == "Human Typing: On"
        assert window.toggle_writing_btn.isChecked() is True

    def test_buttons_carry_state_without_status_label(self, window, d):
        # No status label exists; the buttons carry the state.
        assert not hasattr(window, "status_label")
        window.set_daemon_enabled(False)
        assert window.toggle_daemon_btn.text() == "Daemon: Off"
        assert window.toggle_writing_btn.text() == "Human Typing: Off"


class TestSelectionPreserved:
    def test_selection_survives_language_change(self, window, d):
        sig_item = find_snippet_item(window.snippet_list, "sig")
        window.snippet_list.setCurrentItem(sig_item)

        window.on_language_changed("rust")

        selected = [i.data(0, Qt.UserRole) for i in window.snippet_list.selectedItems()]
        assert selected == ["sig"]
        assert "Trigger: sig" in window.preview_text.toPlainText()

    def test_selection_survives_search_refresh(self, window):
        sig_item = find_snippet_item(window.snippet_list, "sig")
        window.snippet_list.setCurrentItem(sig_item)

        window.search_input.setText("sig")

        selected = [i.data(0, Qt.UserRole) for i in window.snippet_list.selectedItems()]
        assert selected == ["sig"]

    def test_selection_survives_grouping_change(self, window):
        sig_item = find_snippet_item(window.snippet_list, "sig")
        window.snippet_list.setCurrentItem(sig_item)

        window.group_combo.setCurrentIndex(window.group_combo.findData("none"))

        selected = [i.data(0, Qt.UserRole) for i in window.snippet_list.selectedItems()]
        assert selected == ["sig"]


class TestLanguagePersistence:
    def test_language_change_persists_to_config(self, window, d):
        window.on_language_changed("rust")
        assert d.current_language == "rust"
        with open(d.config_file) as f:
            assert json.load(f)["language"] == "rust"

    def test_new_window_uses_config_language(self, qapp, d):
        d.set_language("javascript")
        second = TexterUI(daemon=d)
        try:
            assert second.language_combo.currentText() == "javascript"
        finally:
            second.status_timer.stop()
            second.tray_icon.hide()


class TestHighlightClipping:
    def test_tree_uses_custom_delegate(self, window):
        from ui import SnippetTreeDelegate
        assert isinstance(window.snippet_list.itemDelegate(), SnippetTreeDelegate)

    def test_stylesheet_has_no_full_row_item_rules(self):
        from stylesheet import dark_stylesheet
        # ::item rules make the style engine stretch highlights across the
        # whole row (gutter included); the delegate owns those panels now.
        rules = [
            line.strip() for line in dark_stylesheet.splitlines()
            if line.strip().startswith("QTreeWidget::item") and line.strip().endswith("{")
        ]
        assert rules == []
        # selection colors on the tree would map to the palette highlight the
        # view paints full-row below the delegate. (Property form with ':' so
        # comments mentioning it don't count.)
        assert dark_stylesheet.count("selection-background-color:") == 1


class TestQuitPaths:
    def test_close_button_and_quit_button_share_single_path(self, window, monkeypatch):
        calls = []
        monkeypatch.setattr(window, "quit_app", lambda: calls.append("quit_app"))
        window.close()  # the X button -> closeEvent
        window.quit_button()
        # No extra daemon.stop() in quit_button - both go through quit_app only.
        assert calls == ["quit_app", "quit_app"]

    def test_close_event_stops_daemon_and_saves_snippets(self, window, d, monkeypatch):
        stops = []
        monkeypatch.setattr(d, "stop", lambda: stops.append("stop"))
        saved = []
        monkeypatch.setattr(d, "save_snippets", lambda: saved.append("save"))
        window.close()
        assert stops == ["stop"]
        assert saved == ["save"]
        assert window.really_quit is True


class TestPasteHotkeySetting:
    def test_field_defaults_to_config(self, window, d):
        assert window.paste_hotkey_edit.text() == "ctrl+shift+v"

    def test_save_settings_persists_hotkey(self, window, d):
        window.paste_hotkey_edit.setText("ctrl+alt+v")
        window.save_user_config()
        assert d.config["paste_hotkey"] == "ctrl+alt+v"

    def test_blank_field_falls_back_to_default(self, window, d):
        window.paste_hotkey_edit.setText("   ")
        window.save_user_config()
        assert d.config["paste_hotkey"] == "ctrl+shift+v"


class TestDateFormatSetting:
    def test_fields_default_to_config(self, window, d):
        assert window.date_format_edit.text() == "%Y-%m-%d"
        assert window.time_format_edit.text() == "%H:%M"

    def test_save_settings_persists_formats(self, window, d):
        window.date_format_edit.setText("%d/%m/%Y")
        window.time_format_edit.setText("%H:%M:%S")
        window.save_user_config()
        assert d.config["date_format"] == "%d/%m/%Y"
        assert d.config["time_format"] == "%H:%M:%S"

    def test_blank_formats_fall_back_to_defaults(self, window, d):
        window.date_format_edit.setText("  ")
        window.time_format_edit.setText("")
        window.save_user_config()
        assert d.config["date_format"] == "%Y-%m-%d"
        assert d.config["time_format"] == "%H:%M"


class TestImportConflicts:
    def test_import_reports_overwritten_triggers(self, window, d, monkeypatch, tmp_path):
        d.add_or_update_snippet("sig", {"type": "variable", "value": "OLD"})
        imported = {"sig": {"type": "variable", "value": "NEW"}, "new1": {"type": "variable", "value": "x"}}
        src = tmp_path / "import.json"
        src.write_text(json.dumps(imported))

        prompts = []
        monkeypatch.setattr(
            QMessageBox, "question",
            lambda *a, **k: prompts.append(k.get("text", a[2] if len(a) > 2 else "")) or QMessageBox.No,
        )
        monkeypatch.setattr(
            QFileDialog, "getOpenFileName", lambda *a, **k: (str(src), ""),
        )
        window.import_json()
        assert any("overwrite 1 existing" in p and "'sig'" in p for p in prompts)
        # Aborted: nothing imported.
        assert "new1" not in d.snippets
        assert d.snippets["sig"]["value"] == "OLD"

    def test_import_proceeds_without_conflicts(self, window, d, monkeypatch, tmp_path):
        imported = {"fresh": {"type": "variable", "value": "x"}}
        src = tmp_path / "import.json"
        src.write_text(json.dumps(imported))
        prompts = []
        monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: prompts.append(1) or QMessageBox.Yes)
        monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(src), ""))
        window.import_json()
        assert prompts == []  # no conflict, no prompt
        assert "fresh" in d.snippets

    def test_import_normalizes_legacy_list_format(self, window, d, monkeypatch, tmp_path):
        legacy = [{"trigger": "a1", "type": "variable", "value": "one"}]
        src = tmp_path / "legacy.json"
        src.write_text(json.dumps(legacy))
        monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(src), ""))
        window.import_json()
        assert d.snippets["a1"]["value"] == "one"
        assert "trigger" not in d.snippets["a1"]


class TestDialogEnabledFlag:
    def test_new_snippet_defaults_to_enabled(self, qapp):
        dialog = AddSnippetDialog(None)
        assert dialog.enabled_check.isChecked() is True
        snippet = dialog.get_snippet()
        assert "enabled" not in snippet or snippet["enabled"] is True

    def test_unchecked_writes_enabled_false(self, qapp):
        dialog = AddSnippetDialog(None)
        dialog.enabled_check.setChecked(False)
        snippet = dialog.get_snippet()
        assert snippet["enabled"] is False

    def test_edit_populates_enabled_state(self, qapp):
        dialog = AddSnippetDialog(None, snippet={"trigger": "x", "enabled": False})
        assert dialog.enabled_check.isChecked() is False


class TestDisabledSnippetDisplay:
    def test_disabled_snippet_dimmed_in_tree(self, window, d):
        d.add_or_update_snippet("dim", {"type": "variable", "value": "x", "enabled": False, "category": "test"})
        window.refresh_snippet_list()
        item = find_snippet_item(window.snippet_list, "dim")
        assert item is not None
        assert item.foreground(0).color().name() == "#6c7086"

    def test_enabled_snippet_keeps_type_color(self, window, d):
        window.refresh_snippet_list()
        item = find_snippet_item(window.snippet_list, "sig")
        assert item.foreground(0).color().name() == "#a6e3a1"  # variable green

    def test_preview_notes_disabled_state(self, window, d):
        d.add_or_update_snippet("dim", {"type": "variable", "value": "x", "enabled": False, "category": "test"})
        window.refresh_snippet_list()
        item = find_snippet_item(window.snippet_list, "dim")
        window.snippet_list.setCurrentItem(item)
        assert "DISABLED" in window.preview_text.toPlainText()


class TestTriggerConflicts:
    def test_exact_duplicate_detected(self, window, d):
        conflicts = window.trigger_conflicts("sig")
        assert any("already exists" in c for c in conflicts)

    def test_extending_existing_detected(self, window, d):
        # "sigx" extends "sig": the existing snippet is deferred to a boundary.
        conflicts = window.trigger_conflicts("sigx")
        assert any("extends existing 'sig'" in c for c in conflicts)

    def test_prefix_of_existing_detected(self, window, d):
        # "s" is a prefix of "sig": the new trigger defers to a boundary.
        conflicts = window.trigger_conflicts("s")
        assert any("is a prefix of 'sig'" in c for c in conflicts)

    def test_no_conflicts(self, window, d):
        assert window.trigger_conflicts("zzz") == []

    def test_edit_excludes_own_trigger(self, window, d):
        assert window.trigger_conflicts("sig", exclude="sig") == []

    def test_confirm_blocks_on_no(self, window, d, monkeypatch):
        monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.No)
        assert window._confirm_trigger_conflicts("sig") is False

    def test_confirm_allows_on_yes(self, window, d, monkeypatch):
        monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)
        assert window._confirm_trigger_conflicts("sig") is True

    def test_confirm_passes_through_without_conflicts(self, window, d, monkeypatch):
        monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not prompt")))
        assert window._confirm_trigger_conflicts("zzz") is True


class TestEditSnippet:
    def test_edit_updates_selected_snippet(self, window, d, monkeypatch):
        from PySide6.QtWidgets import QDialog
        from ui import AddSnippetDialog

        sig_item = find_snippet_item(window.snippet_list, "sig")
        window.snippet_list.setCurrentItem(sig_item)
        assert window.snippet_list.selectedItems()[0].data(0, Qt.UserRole) == "sig"

        def fake_exec(dialog):
            dialog.value_input.setPlainText("edited value")
            return QDialog.Accepted

        monkeypatch.setattr(AddSnippetDialog, "exec", fake_exec)
        window.edit_snippet()
        assert d.snippets["sig"]["value"] == "edited value"

    def test_selection_shows_preview(self, window):
        sig_item = find_snippet_item(window.snippet_list, "sig")
        window.snippet_list.setCurrentItem(sig_item)
        assert "Trigger: sig" in window.preview_text.toPlainText()

    def test_edit_with_no_selection_is_noop(self, window, d):
        window.snippet_list.clearSelection()
        window.edit_snippet()  # must not raise


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
        assert window.toggle_daemon_btn.text() == "Daemon: Off"
        assert window.tray_icon.toolTip() == "Texter - Paused"

    def test_button_toggle_round_trip(self, window, d):
        window.toggle_daemon(False)
        assert d.enabled is False
        assert window.toggle_daemon_btn.isChecked() is False
        assert window.toggle_daemon_btn.text() == "Daemon: Off"

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
