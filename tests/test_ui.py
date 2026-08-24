import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

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
