# texter_mvp/ui.py
import sys
import csv
import html
import json
import os
import shutil
import threading
import re
from datetime import datetime
from copy import deepcopy
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QTextEdit, QTextBrowser, QPushButton, QComboBox,
    QListWidget, QListWidgetItem, QDialog, QFormLayout,
    QMessageBox, QFileDialog, QGroupBox, QSystemTrayIcon, QMenu,
    QCheckBox, QTreeWidget, QTreeWidgetItem, QAbstractItemView,
    QStyledItemDelegate, QStyle, QStyleOptionViewItem,
    QTableWidget, QTableWidgetItem, QHeaderView, QDialogButtonBox,
    QRadioButton, QScrollArea, QFrame, QInputDialog
)
from PySide6.QtCore import Qt, QTimer, Signal, QRectF, QRect, QSettings, QEvent, QSize, QUrl
from PySide6.QtGui import (
    QIcon, QAction, QPixmap, QPainter, QColor, QPalette, QPainterPath,
    QShortcut, QKeySequence,
)

from snippet_helpers import (
    BUILTIN_PLACEHOLDERS as _BUILTIN_PLACEHOLDERS,
    SETTING_PLACEHOLDER_RE as _SETTING_PLACEHOLDER_RE,
    _fuzzy_score,
    _normalized_expansion,
    history_entry,
)

BUILTIN_PLACEHOLDERS = _BUILTIN_PLACEHOLDERS
SETTING_PLACEHOLDER_RE = _SETTING_PLACEHOLDER_RE

from widgets import (
    TREE_BACKGROUND,
    create_status_icon,
    SnippetTreeDelegate,
    SnippetTreeWidget,
    UsedByTableWidgetItem,
    SortableTableWidgetItem,
    SnippetTableWidget,
)

from dialogs import (
    AddSnippetDialog,
    PromptValuesDialog,
    QuickInsertDialog,
    BackupsDialog,
    BackupSnippetRestoreDialog,
    TrashDialog,
    SnippetHistoryDialog,
    PackExportDialog,
    PackConflictDialog,
    CleanupDialog,
    CategoryTagDialog,
)


STARTER_SNIPPETS = {
    ";sig": {"type": "variable", "value": "Best regards,\n{name}", "category": "Starter", "tags": ["starter"], "usage": 0, "last_used": None, "enabled": True},
    ";today": {"type": "variable", "value": "Today is {date}.", "category": "Starter", "tags": ["starter"], "usage": 0, "last_used": None, "enabled": True},
    ";thanks": {"type": "variable", "value": "Thanks for getting back to me \u2014 I\'ll follow up shortly.\nBest regards,\n{name}", "category": "Starter", "tags": ["starter"], "usage": 0, "last_used": None, "enabled": True},
    ";tone": {"type": "variable", "value": "{choice:Formal|Friendly} regards,\n{name}", "category": "Starter", "tags": ["starter"], "usage": 0, "last_used": None, "enabled": True},
}


class TexterUI(QMainWindow):
    quit_requested = Signal()
    prompt_requested = Signal(object)
    # Emitted by the daemon's keyboard listener thread; Qt delivers it
    # to handle_daemon_command on the GUI thread via a queued connection.
    daemon_command = Signal(str)

    def __init__(self, daemon=None):
        super().__init__()
        self.daemon = daemon
        if daemon:
            daemon.ui = self
        self.app = None
        self._focus_app = None
        # UI-improvement state (defaults; setup_ui / refresh fill them in).
        self._quick_filter = "all"
        self._density = "comfortable"
        self._text_size = "medium"
        self._right_min_height = 0
        self._search_regex = False
        self._search_case_sensitive = False
        self._search_regex_error = None
        self._preview_mode = "rendered"
        self._preview_raw_text = ""
        self._preview_rendered_text = ""
        self._settings_baseline = None
        self._app_settings = QSettings("Landaluce", "Textr")
        # Undo stack (item 1): (label, snippets snapshot, trash triggers).
        self._undo_stack = []
        # Last usage stamp seen by the live-counts poll (None = not
        # initialised yet); see poll_daemon_status.
        self._last_usage_stamp = None
        # Last search/filter result, for CSV export of visible rows.
        self._last_filtered_items = []
        self.quit_requested.connect(self.quit_app)
        self.daemon_command.connect(self.handle_daemon_command, Qt.QueuedConnection)
        self.prompt_requested.connect(self._handle_prompt_request, Qt.QueuedConnection)
        if daemon:
            # Expansion worker thread -> GUI thread prompt (see below).
            daemon.prompt_handler = self._prompt_from_daemon_thread
        self.setup_ui()
        self._track_app_focus(QApplication.instance())

    def set_app(self, app):
        """Set the QApplication reference"""
        self.app = app
        self._track_app_focus(app)

    def _track_app_focus(self, app):
        """Subscribe to focus changes exactly once per QApplication."""
        if app is not None and app is not self._focus_app:
            self._focus_app = app
            app.focusChanged.connect(self._on_app_focus_changed)

    def _on_app_focus_changed(self, old, now):
        """Suppress trigger detection while the manager itself is focused.

        The daemon's global keyboard capture sees keystrokes aimed at this
        window too. Without this, typing inside the snippet editor (add/edit
        dialogs, search box) could fire an existing snippet - including
        control commands like toggle_pause or shutdown - instead of editing
        text. QApplication.focusChanged reports None when focus leaves this
        application, so the flag follows the user between windows.
        """
        if self.daemon:
            self.daemon.ui_focused = now is not None

    def handle_daemon_command(self, action):
        """Run daemon-requested UI actions on the GUI thread.

        The daemon's keyboard listener thread emits daemon_command instead of
        touching widgets directly (Qt widgets are not thread-safe)."""
        if action in ("ui", "top"):
            self.bring_to_front()
        elif action == "picker":
            self.show_picker(
                getattr(self.daemon, "_picker_target_window", None),
                from_hotkey=True,
            )
        elif action == "hide":
            self.hide_window()
        elif action == "refresh":
            self.refresh_snippet_list()
        elif action == "restart":
            self.restart_app()
        elif action == "sync_writing_mode":
            if not self.daemon:
                return
            self.toggle_writing_btn.blockSignals(True)
            self.toggle_writing_btn.setChecked(self.daemon.writing_mode)
            self.toggle_writing_btn.blockSignals(False)
            self._sync_toggle_text()

    def quit_app(self):
        """Quit the entire application"""
        print("UI: Quitting application...")
        if not self._confirm_unsaved_settings():
            return
        self.really_quit = True
        if self.daemon:
            self.daemon.save_snippets()
            self.daemon.stop()
        if hasattr(self, 'tray_icon'):
            self.tray_icon.hide()
        self.close()
        QApplication.quit()

    def _confirm_unsaved_settings(self):
        """Ask about unsaved settings before closing/quitting.

        Returns True to proceed. Save re-runs the normal save path; if
        validation fails there the settings are still dirty, so closing
        is refused. Discard closes without saving (for close-to-tray the
        edits stay in the hidden window's fields until the next save).
        """
        if getattr(self, "_settings_baseline", None) is None:
            return True
        if self._settings_snapshot() == self._settings_baseline:
            return True
        reply = QMessageBox.question(
            self,
            "Unsaved settings",
            "You have unsaved settings changes.\nSave them before closing?",
            QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel,
            QMessageBox.Save,
        )
        if reply == QMessageBox.Cancel:
            return False
        if reply == QMessageBox.Save:
            self.save_user_config()
            return self._settings_snapshot() == self._settings_baseline
        return True

    def resizeEvent(self, event):
        """Window resized: re-measure the all-expanded panel height."""
        super().resizeEvent(event)
        self._sync_right_content_height()

    def closeEvent(self, event):
        if not (hasattr(self, "really_quit") and self.really_quit):
            if not self._confirm_unsaved_settings():
                event.ignore()
                return
        # Persist window state on every close, including the
        # quit_app() path, which re-enters here with really_quit set.
        try:
            self._save_window_state()
        except Exception:
            pass
        if hasattr(self, 'really_quit') and self.really_quit:
            event.accept()
            return
        # Close-to-tray (item 2): when configured, closing hides the
        # manager instead of quitting; the daemon keeps running and the
        # tray menu's Show Manager brings it back.
        if self.daemon and self.daemon.config.get("close_behavior") == "tray":
            event.ignore()
            self.hide_window()
            if hasattr(self, "tray_icon"):
                self.tray_icon.showMessage(
                    "Textr", "Textr is still running in the tray.",
                    QSystemTrayIcon.MessageIcon.Information, 3000,
                )
            return
        self.quit_app()

    SETTING_PLACEHOLDER_RE = _SETTING_PLACEHOLDER_RE
    # Built-ins from daemon.get_context() (name, email, date, time, iso, week)
    # plus clipboard from daemon.render_template(). These always win over a
    # custom setting of the same name, so they must stay reserved.
    BUILTIN_PLACEHOLDERS = _BUILTIN_PLACEHOLDERS

    def add_user_setting_row(self, key="", value=""):
        # Sorting must be off while the row is filled one cell at a time:
        # with it on, the first setItem can trigger a resort that moves
        # the half-filled row, so the value lands on a different row
        # from its key (the preview fill-in hit exactly this).
        was_sorting = self.user_settings_table.isSortingEnabled()
        self.user_settings_table.setSortingEnabled(False)
        row = self.user_settings_table.rowCount()
        self.user_settings_table.insertRow(row)
        self.user_settings_table.setItem(row, 0, QTableWidgetItem(key))
        self.user_settings_table.setItem(row, 1, QTableWidgetItem(value))
        used_by_item = UsedByTableWidgetItem("")
        used_by_item.setData(Qt.ItemDataRole.UserRole, (0, 0))
        used_by_item.setFlags(
            Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled
        )
        self.user_settings_table.setItem(row, 2, used_by_item)
        self.user_settings_table.setSortingEnabled(was_sorting)
        return row

    def add_user_setting(self):
        row = self.add_user_setting_row()
        item = self.user_settings_table.item(row, 0)
        self.user_settings_table.setCurrentItem(item)
        self.user_settings_table.setFocus()
        self.user_settings_table.editItem(item)
        self._update_settings_dirty()

    def remove_user_settings(self):
        rows = {
            index.row()
            for index in self.user_settings_table.selectionModel().selectedRows()
        }
        for row in sorted(rows, reverse=True):
            self.user_settings_table.removeRow(row)
        self._update_settings_dirty()

    def collect_user_settings(self):
        settings = {}
        for row in range(self.user_settings_table.rowCount()):
            key_item = self.user_settings_table.item(row, 0)
            value_item = self.user_settings_table.item(row, 1)
            key = key_item.text().strip() if key_item else ""
            value = value_item.text() if value_item else ""
            if not key and not value:
                continue
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
                raise ValueError(
                    f"Row {row + 1}: use a placeholder name such as company or first_name."
                )
            if key in self.BUILTIN_PLACEHOLDERS:
                raise ValueError(f"'{key}' is reserved for a built-in placeholder.")
            if key in settings:
                raise ValueError(f"Duplicate placeholder: '{key}'.")
            settings[key] = value
        return settings

    def get_snippet_setting_references(self):
        if not self.daemon:
            return {}
        with self.daemon.snippets_lock:
            snippets = deepcopy(self.daemon.snippets)
        references = {}
        for trigger, snippet in snippets.items():
            if not isinstance(snippet, dict):
                continue
            kind = snippet.get("type", "variable")
            texts = []
            if kind == "variable":
                texts.append(snippet.get("value", ""))
            elif kind == "template":
                languages = snippet.get("languages", {})
                if isinstance(languages, dict):
                    texts.extend(languages.values())
            for text in texts:
                if isinstance(text, str):
                    for key in self.SETTING_PLACEHOLDER_RE.findall(text):
                        references.setdefault(key, set()).add(str(trigger))
        return references

    def refresh_user_setting_usage(self):
        """Fill the read-only "Used by" column for every custom setting row.
    
                For each setting this shows how many times it is referenced in total
                and in how many snippets, plus which snippet triggers use it, e.g.
                "3 uses in 2 snippets: hello, sig". A setting no snippet uses shows
                "0 snippets". This column is display-only: collect_user_settings()
                reads only columns 0 and 1, so it can never be saved into config.
                """
        if not hasattr(self, "user_settings_table"):
            return
        was_sorting = self.user_settings_table.isSortingEnabled()
        self.user_settings_table.setSortingEnabled(False)
        triggers_by_key = {}
        uses_by_key = {}
        if self.daemon:
            with self.daemon.snippets_lock:
                snippets = deepcopy(self.daemon.snippets)
            for trigger, snippet in snippets.items():
                if not isinstance(snippet, dict):
                    continue
                kind = snippet.get("type", "variable")
                texts = []
                if kind == "variable":
                    texts.append(snippet.get("value", ""))
                elif kind == "template":
                    languages = snippet.get("languages", {})
                    if isinstance(languages, dict):
                        texts.extend(languages.values())
                for text in texts:
                    if not isinstance(text, str):
                        continue
                    for key in self.SETTING_PLACEHOLDER_RE.findall(text):
                        triggers_by_key.setdefault(key, set()).add(str(trigger))
                        uses_by_key[key] = uses_by_key.get(key, 0) + 1
        for row in range(self.user_settings_table.rowCount()):
            key_item = self.user_settings_table.item(row, 0)
            key = key_item.text().strip() if key_item else ""
            triggers = sorted(triggers_by_key.get(key, set())) if key else []
            if triggers:
                snippet_word = "snippet" if len(triggers) == 1 else "snippets"
                use_word = "use" if uses_by_key[key] == 1 else "uses"
                text = (
                    f"{len(triggers)} {snippet_word}, "
                    f"{uses_by_key[key]} {use_word}: " + ", ".join(triggers)
                )
                tooltip = "Used by snippets: " + ", ".join(triggers)
            else:
                text = "0 snippets"
                tooltip = "Not used by any snippet"
            used_by_item = self.user_settings_table.item(row, 2)
            if used_by_item is None:
                used_by_item = UsedByTableWidgetItem("")
                used_by_item.setFlags(
                    Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled
                )
                self.user_settings_table.setItem(row, 2, used_by_item)
            used_by_item.setData(
                Qt.ItemDataRole.UserRole,
                (len(triggers), uses_by_key.get(key, 0)),
            )
            used_by_item.setText(text)
            used_by_item.setToolTip(tooltip)
        self.user_settings_table.setSortingEnabled(was_sorting)

    def find_missing_user_settings(self, checked=False, *, notify=True):
        if not self.daemon:
            return
        self.user_settings_table.setSortingEnabled(False)
        references = self.get_snippet_setting_references()
        existing_rows = {}
        for row in range(self.user_settings_table.rowCount()):
            item = self.user_settings_table.item(row, 0)
            key = item.text().strip() if item else ""
            if key:
                existing_rows[key] = row
        added = []
        unset = []
        for key in sorted(references):
            if key in self.BUILTIN_PLACEHOLDERS:
                continue
            if key not in existing_rows:
                existing_rows[key] = self.add_user_setting_row(key, "")
                added.append(key)
            row = existing_rows[key]
            key_item = self.user_settings_table.item(row, 0)
            value_item = self.user_settings_table.item(row, 1)
            if value_item is None:
                value_item = QTableWidgetItem("")
                self.user_settings_table.setItem(row, 1, value_item)
            tooltip = "Used by snippets: " + ", ".join(sorted(references[key]))
            key_item.setToolTip(tooltip)
            if not value_item.text().strip():
                unset.append(key)
                value_item.setToolTip(
                    "No value set. Enter a value, then click Save Settings.\n"
                    + tooltip
                )
            else:
                value_item.setToolTip(tooltip)
        if unset and notify:
            item = self.user_settings_table.item(existing_rows[unset[0]], 1)
            self.user_settings_table.setCurrentItem(item)
            self.user_settings_table.scrollToItem(item)
        if notify:
            if unset:
                message = (
                    f"Added {len(added)} new placeholder(s).\n"
                    f"{len(unset)} referenced placeholder(s) have no value:\n\n"
                    + ", ".join(unset)
                    + "\n\nFill in their values, then click Save Settings."
                )
            else:
                message = "All detected custom placeholders already have values."
            QMessageBox.information(self, "Placeholder values", message)
        elif added:
            self.statusBar().showMessage(
                f"Found {len(added)} missing custom setting(s). "
                "Fill them in and click Save Settings.", 8000
            )
        self.refresh_user_setting_usage()
        self.user_settings_table.setSortingEnabled(True)

    # ---- Prompt-on-expand ---------------------------------------------
    @staticmethod
    def _split_prompt_payload(payload):
        """Normalise a prompt payload to (missing_keys, choices).

        The daemon sends {"missing": [...], "choices": [(label, opts)]}.
        A plain list means missing keys only (older callers).
        """
        if isinstance(payload, dict):
            return list(payload.get("missing") or []), list(
                payload.get("choices") or []
            )
        return list(payload or []), []

    def _prompt_from_daemon_thread(self, payload):
        """Prompt handler called from the daemon expansion worker.

        Shows the dialog on the GUI thread via a queued signal and waits
        for it. Returns the values dict, or None on cancel/timeout.
        """
        self._prompt_event = threading.Event()
        self._prompt_result = None
        self.prompt_requested.emit(payload)
        if not self._prompt_event.wait(timeout=120):
            return None
        return self._prompt_result

    def _handle_prompt_request(self, payload):
        missing, choices = self._split_prompt_payload(payload)
        # Lower the manager so it does not steal focus back when the
        # prompt dialog closes; xdotool restore below is the exact fix.
        try:
            self.lower()
        except Exception:
            pass
        dialog = PromptValuesDialog(missing, choices)
        if dialog.exec() == QDialog.Accepted:
            values = dialog.values()
            if dialog.save_checked():
                self.sync_prompted_settings(values)
            self._prompt_result = values
        else:
            self._prompt_result = None
        # Restore the target window BEFORE waking the expansion worker,
        # or the worker's paste lands in the manager window.
        if self.daemon:
            self.daemon._restore_active_window(
                getattr(self.daemon, "_prompt_target_window", None)
            )
        if getattr(self, "_prompt_event", None) is not None:
            self._prompt_event.set()

    def prompt_for_values_sync(self, missing, choices=None):
        """Prompt on the GUI thread (quick picker path). None = cancelled."""
        if isinstance(missing, dict):
            missing, choices = self._split_prompt_payload(missing)
        dialog = PromptValuesDialog(missing, choices)
        if dialog.exec() != QDialog.Accepted:
            return None
        values = dialog.values()
        if dialog.save_checked():
            self.sync_prompted_settings(values)
        return values

    def sync_prompted_settings(self, values):
        """Merge prompted values into user_settings and the settings table."""
        if not self.daemon:
            return
        settings = self.daemon.config.get("user_settings", {})
        if not isinstance(settings, dict):
            settings = {}
        changed = False
        for key, value in values.items():
            # Choice-placeholder keys ("{choice:A|B}") are per-expansion
            # picks, not settings; saving one would corrupt config.
            if isinstance(key, str) and key.startswith("{choice:"):
                continue
            if value and str(value).strip():
                settings[key] = value
                changed = True
        if not changed:
            return
        self.daemon.config["user_settings"] = settings
        self.daemon.save_config()
        if hasattr(self, "user_settings_table"):
            existing = {}
            for row in range(self.user_settings_table.rowCount()):
                item = self.user_settings_table.item(row, 0)
                if item:
                    existing[item.text().strip()] = row
            for key, value in values.items():
                if not value or not str(value).strip():
                    continue
                if key in existing:
                    self.user_settings_table.setItem(
                        existing[key], 1, QTableWidgetItem(value)
                    )
                else:
                    self.add_user_setting_row(key, value)
            self.refresh_user_setting_usage()

    # ---- Quick picker ----------------------------------------------------
    def show_picker(self, target_window=None, from_hotkey=False):
        if not self.daemon:
            return
        # Lower the manager before the picker opens: after the picker
        # closes, the window on top should be the target app, not the
        # manager (which Qt would otherwise reactivate).
        try:
            self.lower()
        except Exception:
            pass
        dialog = QuickInsertDialog(
            self.daemon, self, target_window, from_hotkey
        )
        result = dialog.exec()
        if result != QDialog.Accepted or not dialog.pending_insert:
            return
        trigger, final = dialog.pending_insert
        # Restore AFTER the dialog has closed: this is the focus action
        # that must win. Restoring before accept() (the old code) was
        # overwritten by Qt reactivating the manager on close, which is
        # why the paste landed in whatever window was behind the picker.
        # Lower the manager again first, so even without xdotool the
        # target app is the topmost window left.
        try:
            self.lower()
        except Exception:
            pass
        restored = False
        if target_window and self.daemon:
            restored = self.daemon._restore_active_window(target_window)
        self._schedule_picker_insert(
            trigger, final, 500 if restored else 400
        )

    def _insert_picker_selection(self, trigger, text):
        if self.daemon:
            self.daemon.insert_text(trigger, text)

    def _schedule_picker_insert(self, trigger, text, delay_ms):
        """Schedule a picker insert on a manager-owned timer.

        The picker dialog is created as a temporary in show_picker(),
        so a timer created by the dialog itself may be discarded with
        it once exec() returns. A timer owned by the manager (which
        lives for the whole app session) cannot be lost that way.
        """
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.timeout.connect(
            lambda: self._insert_picker_selection(trigger, text)
        )
        # Keep a Python reference until it fires; otherwise the timer
        # object itself could be collected before firing.
        self._picker_insert_timer = timer
        timer.start(delay_ms)

    # ---- Per-snippet history (item 3) -----------------------------------

    @staticmethod
    def _history_entry(snippet):
        """One history entry: when it was saved + the snippet as stored."""
        return history_entry(snippet)
    def _with_history(self, old_snippet, new_snippet):
        """Return `new_snippet` carrying the old version in its history.

        History is capped at 5 entries, newest first, and lives inside
        the snippet dict, so it is saved by save_snippets() and survives
        restarts (unlike the session-only undo stack).
        """
        history = list((old_snippet or {}).get("history") or [])
        history.insert(0, self._history_entry(old_snippet))
        new_snippet["history"] = history[:5]
        return new_snippet

    def show_snippet_history(self, trigger=None):
        """History dialog for the selected (or given) snippet."""
        trigger = trigger or self.selected_trigger()
        if not trigger or not self.daemon:
            return
        history = (self.daemon.snippets.get(trigger, {}) or {}).get("history")
        if not history:
            QMessageBox.information(
                self, "History",
                f"'{trigger}' has no earlier versions yet. "
                "A version is kept every time you edit it.",
            )
            return
        if SnippetHistoryDialog(self, trigger).exec() == QDialog.Accepted:
            self.refresh_snippet_list()

    # ---- Tray quick-insert (item 5) + clipboard capture (item 6) --------

    def _refresh_tray_insert_menu(self):
        """Rebuild the tray's Insert submenu: Favourites, then Recent.

        Rebuilt on every open (aboutToShow), so usage and favourites
        are always current. Only insertable (variable/template,
        enabled) snippets appear, at most 5 per section.
        """
        menu = getattr(self, "tray_insert_menu", None)
        if menu is None or not self.daemon:
            return
        menu.clear()
        with self.daemon.snippets_lock:
            snippets = dict(self.daemon.snippets)
        insertable = {
            t: s for t, s in snippets.items()
            if isinstance(s, dict)
            and s.get("type") in ("variable", "template")
            and s.get("enabled", True)
        }
        favourites = sorted(
            (t for t, s in insertable.items() if s.get("favorite")),
            key=lambda t: (-int(insertable[t].get("usage", 0) or 0), t.lower()),
        )[:5]
        recent = sorted(
            (t for t, s in insertable.items()
             if (s.get("last_used") or "never") != "never"
             and t not in favourites),
            key=lambda t: (insertable[t].get("last_used") or "", t.lower()),
            reverse=True,
        )[:5]
        if not favourites and not recent:
            _empty = menu.addAction("(No snippets yet)")
            _empty.setEnabled(False)
            return
        for title, group in (("Favourites", favourites), ("Recent", recent)):
            if not group:
                continue
            menu.addSection(title)
            for trigger in group:
                action = menu.addAction(trigger)
                action.triggered.connect(
                    lambda _c, trig=trigger: self._tray_insert_trigger(trig)
                )

    def _tray_insert_trigger(self, trigger):
        """Insert one snippet from the tray menu (prompting if needed)."""
        if not self.daemon:
            return
        snippet = self.daemon.snippets.get(trigger)
        if not snippet or not snippet.get("enabled", True):
            return
        raw = self._raw_expansion(snippet)
        if raw.startswith("(No template for"):
            self.statusBar().showMessage(
                f"No {self.language_combo.currentText()} template for '{trigger}'", 3000
            )
            return
        missing = []
        _missing_fn = getattr(self.daemon, "missing_placeholders", None)
        if callable(_missing_fn):
            missing = _missing_fn(raw)
        choices = []
        _choice_fn = getattr(self.daemon, "choice_placeholders", None)
        if callable(_choice_fn):
            choices = _choice_fn(raw)
        values = {}
        if missing or choices:
            values = self.prompt_for_values_sync(missing, choices)
            if values is None:
                return
        final = self.daemon.render_template(raw, prompt_values=values)
        self.hide_window()
        self._schedule_picker_insert(trigger, final, 300)

    def new_snippet_from_clipboard(self):
        """Open Add with the clipboard text prefilled as the value."""
        if not self.daemon:
            return
        text = QApplication.clipboard().text().strip()
        if not text:
            self.statusBar().showMessage(
                "Clipboard is empty - nothing to make a snippet from", 3000
            )
            return
        categories = self.daemon.get_categories() if self.daemon else []
        dialog = AddSnippetDialog(
            self,
            snippet={"type": "variable", "value": text, "category": "uncategorized"},
            categories=categories,
            daemon=self.daemon,
        )
        if dialog.exec() == QDialog.Accepted:
            snippet = dialog.get_snippet()
            if snippet and self.daemon:
                trigger = snippet.pop("trigger")
                if not self._confirm_trigger_conflicts(trigger):
                    return
                self._push_undo(f"Add snippet '{trigger}'")
                self.daemon.add_or_update_snippet(trigger, snippet)
                self.refresh_snippet_list()
                print(f"UI: Added snippet '{trigger}' from clipboard (saved)")

    # ---- Backups / trash / cleanup ---------------------------------------
    def show_backups(self):
        BackupsDialog(self).exec()

    def show_trash(self):
        TrashDialog(self).exec()
        self.refresh_snippet_list()

    def show_cleanup(self):
        CleanupDialog(self).exec()
        self.refresh_snippet_list()

    # ---- Pack export / import --------------------------------------------
    def export_pack(self):
        if not self.daemon:
            return
        dialog = PackExportDialog(self)
        if dialog.exec() != QDialog.Accepted:
            return
        triggers = dialog.selected_triggers()
        if not triggers:
            QMessageBox.information(self, "Export Pack", "No snippets selected.")
            return
        file_path, _ = QFileDialog.getSaveFileName(
            self, "Export Pack", "", "JSON Files (*.json)"
        )
        if not file_path:
            return
        try:
            with self.daemon.snippets_lock:
                snippets = {
                    t: {k: v for k, v in self.daemon.snippets[t].items()
                        if k not in ("usage", "last_used", "history")}
                    for t in triggers if t in self.daemon.snippets
                }
            pack = {"format": "textr-pack", "version": 1, "snippets": snippets}
            with open(file_path, "w") as f:
                json.dump(pack, f, indent=2)
            self.statusBar().showMessage(
                f"Exported pack with {len(snippets)} snippet(s)", 3000
            )
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to export pack: {e}")

    def import_starter_snippets(self):
        """Import the starter snippets, skipping triggers already present."""
        if not self.daemon:
            return
        missing = {
            trigger: deepcopy(snippet)
            for trigger, snippet in STARTER_SNIPPETS.items()
            if trigger not in self.daemon.snippets
        }
        if not missing:
            self.statusBar().showMessage("Starter snippets are already in your library", 3000)
            return
        self.daemon.import_snippets(missing)
        self.refresh_snippet_list()
        skipped = len(STARTER_SNIPPETS) - len(missing)
        message = f"Imported {len(missing)} starter snippet(s)"
        if skipped:
            message += f" ({skipped} already in your library)"
        self.statusBar().showMessage(message, 4000)

    def import_pack(self):
        if not self.daemon:
            return
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Import Pack", "", "JSON Files (*.json)"
        )
        if not file_path:
            return
        try:
            with open(file_path, "r") as f:
                raw = json.load(f)
            if isinstance(raw, dict) and isinstance(raw.get("snippets"), dict):
                snippets = raw["snippets"]
            elif isinstance(raw, dict):
                snippets = raw
            else:
                raise ValueError("file is neither a pack nor a snippet dict")
            if not snippets:
                QMessageBox.information(self, "Import Pack", "No snippets found in the file.")
                return
            with self.daemon.snippets_lock:
                existing = dict(self.daemon.snippets)

            def _clean(snippet):
                if not isinstance(snippet, dict):
                    return snippet
                return {k: v for k, v in snippet.items()
                        if k not in ("usage", "last_used")}

            conflicts = sorted(
                t for t in snippets
                if t in existing and _clean(existing[t]) != _clean(snippets[t])
            )
            to_import = dict(snippets)
            if conflicts:
                dialog = PackConflictDialog(conflicts)
                if dialog.exec() != QDialog.Accepted:
                    return
                choice = dialog.choice()
                if choice == "skip":
                    for t in conflicts:
                        to_import.pop(t, None)
                elif choice == "rename":
                    renamed = {}
                    for t in snippets:
                        new_t = t
                        if t in conflicts:
                            n = 2
                            while new_t in existing or new_t in renamed:
                                new_t = f"{t}-{n}"
                                n += 1
                        renamed[new_t] = snippets[t]
                    to_import = renamed
                # overwrite: to_import stays as-is
            if not to_import:
                QMessageBox.information(
                    self, "Import Pack", "Nothing to import (all conflicts skipped)."
                )
                return
            to_import = {
                t: ({k: v for k, v in sn.items() if k not in ("usage", "last_used")}
                    if isinstance(sn, dict) else sn)
                for t, sn in to_import.items()
            }
            self._push_undo(f"Import pack ({len(to_import)} snippet(s))")
            self.daemon.import_snippets(to_import)
            self.refresh_snippet_list()
            self.find_missing_user_settings(notify=False)
            self.statusBar().showMessage(
                f"Imported pack: {len(to_import)} snippet(s)", 3000
            )
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to import pack: {e}")

    def save_user_config(self):
        if not self.daemon:
            QMessageBox.warning(self, "Settings unavailable", "The daemon is not connected.")
            return
        try:
            custom_settings = self.collect_user_settings()
        except ValueError as exc:
            QMessageBox.warning(self, "Invalid setting", str(exc))
            return
        updates = {
            "name": self.name_edit.text().strip(),
            "email": self.email_edit.text().strip(),
            "layout": self.layout_combo.currentData(),
            "paste_hotkey": self.paste_hotkey_edit.text().strip() or "ctrl+shift+v",
            "picker_hotkey": self.picker_hotkey_edit.text().strip().lower() or "ctrl+alt+space",
            "close_behavior": self.close_behavior_combo.currentData(),
            "date_format": self.date_format_edit.text().strip() or "%Y-%m-%d",
            "time_format": self.time_format_edit.text().strip() or "%H:%M",
            "user_settings": custom_settings,
        }
        previous_config = dict(self.daemon.config)
        try:
            self.daemon.config.update(updates)
            self.daemon.save_config()
        except Exception as exc:
            self.daemon.config.clear()
            self.daemon.config.update(previous_config)
            QMessageBox.critical(self, "Save failed", f"Could not save settings:\n{exc}")
            return
        self.on_selection_changed()
        self.statusBar().showMessage("Settings saved", 3000)
        self._settings_baseline = self._settings_snapshot()
        self._update_settings_dirty()
        # Apply the picker shortcut without restarting (item 2). When
        # the daemon cannot re-register (old daemon, or a Wayland evdev
        # session), the saved combo still takes effect on next start.
        self._sync_picker_button_label()
        restart = getattr(self.daemon, "restart_hotkey_listener", None)
        if callable(restart):
            if restart():
                self.statusBar().showMessage(
                    "Settings saved — Quick Insert shortcut updated", 3000
                )
            else:
                self.statusBar().showMessage(
                    "Settings saved — shortcut applies on next start (X11 only)", 4000
                )

    def poll_daemon_status(self):
        if not self.daemon:
            return
        if self.daemon.enabled != self.last_enabled_state:
            # State changed outside the UI (hotkey or command snippet):
            # resync button, tray icon, and status label.
            self.set_daemon_enabled(self.daemon.enabled)

        # Check if language changed in daemon (e.g., via slj or slp commands)
        current_lang = self.language_combo.currentText()
        if self.daemon.current_language != current_lang:
            self.language_combo.blockSignals(True)
            self.language_combo.setCurrentText(self.daemon.current_language)
            self.language_combo.blockSignals(False)
            self.refresh_snippet_list()
            self.on_selection_changed()
            print(f"UI: Programming language switched to {self.daemon.current_language}")

        # Live counts: usage/last_used change in the daemon on every
        # expansion (typed trigger, picker, tray) with no UI call, so
        # without this the Uses column, group counts, health line and
        # preview Used line stay stale until a manual refresh. A stamp
        # bump rebuilds them, preserving the current selection.
        # Deferred while a modal dialog is open, so a rebuild never
        # runs underneath an Add/Edit dialog; the next poll retries.
        _stamp_fn = getattr(self.daemon, "usage_stamp", None)
        if callable(_stamp_fn):
            try:
                _stamp = _stamp_fn()
            except Exception:
                _stamp = None
            if _stamp is not None:
                _last = getattr(self, "_last_usage_stamp", None)
                if _last is None:
                    self._last_usage_stamp = _stamp
                elif _stamp != _last:
                    if QApplication.activeModalWidget() is None:
                        self._last_usage_stamp = _stamp
                        self.refresh_snippet_list()
                        self.on_selection_changed()

    def setup_ui(self):
        self.setWindowTitle("Texter - Snippet Manager")
        self.setMinimumSize(900, 600)

        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QHBoxLayout(central)

        # Left panel: Snippet list
        left_group = QGroupBox("Snippets")
        left_layout = QVBoxLayout(left_group)

        # Category filter dropdown
        cat_layout = QHBoxLayout()
        cat_layout.addWidget(QLabel("Category:"))
        self.category_combo = QComboBox()
        self.category_combo.addItems(["all"])  # Will be populated in refresh_snippet_list
        self.category_combo.currentTextChanged.connect(self.on_category_changed)
        cat_layout.addWidget(self.category_combo)
        self.manage_categories_btn = QPushButton("Manage\u2026")
        self.manage_categories_btn.setToolTip("Rename or delete categories and tags")
        self.manage_categories_btn.clicked.connect(self.show_manage_categories)
        cat_layout.addWidget(self.manage_categories_btn)
        left_layout.addLayout(cat_layout)

        # Search box
        search_layout = QHBoxLayout()
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Search triggers or expansion text...")
        self.search_input.textChanged.connect(self.refresh_snippet_list)
        search_layout.addWidget(self.search_input)
        self.search_regex_btn = QPushButton(".*")
        self.search_regex_btn.setCheckable(True)
        self.search_regex_btn.setToolTip("Treat the search text as a regular expression")
        self.search_regex_btn.toggled.connect(self._on_search_mode_changed)
        search_layout.addWidget(self.search_regex_btn)
        self.search_case_btn = QPushButton("Aa")
        self.search_case_btn.setCheckable(True)
        self.search_case_btn.setToolTip("Case-sensitive search")
        self.search_case_btn.toggled.connect(self._on_search_mode_changed)
        search_layout.addWidget(self.search_case_btn)
        left_layout.addLayout(search_layout)

        # Quick filters (item 2): one-tap filters above the list.
        # Clicking the active chip again clears back to All.
        self._filter_buttons = {}
        filter_layout = QHBoxLayout()
        for _key, _label in (
            ("all", "All"),
            ("favourites", "\u2605 Favourites"),
            ("variable", "Variable"),
            ("template", "Template"),
            ("command", "Command"),
            ("disabled", "Disabled"),
            ("unused", "Never used"),
        ):
            _btn = QPushButton(_label)
            _btn.setObjectName("filter_chip")
            _btn.setCheckable(True)
            _btn.setChecked(_key == self._quick_filter)
            _btn.clicked.connect(lambda _c, k=_key: self._set_quick_filter(k))
            filter_layout.addWidget(_btn)
            self._filter_buttons[_key] = _btn
        filter_layout.addStretch(1)
        self.filter_count_label = QLabel("")
        filter_layout.addWidget(self.filter_count_label)
        left_layout.addLayout(filter_layout)

        # Grouping, sort and view on ONE row (declutter): Then-by
        # only applies to the tree, so it hides in Table view.
        view_layout = QHBoxLayout()
        view_layout.addWidget(QLabel("Group:"))
        self.group_combo = QComboBox()
        self.group_combo.addItem("None", "none")
        self.group_combo.addItem("By category", "category")
        self.group_combo.addItem("By type", "type")
        self.group_combo.addItem("By tag", "tag")
        self.group_combo.setCurrentIndex(1)  # category groups by default
        self.group_combo.currentIndexChanged.connect(self._on_group_mode_changed)
        view_layout.addWidget(self.group_combo)
        self.thenby_label = QLabel("Then by:")
        view_layout.addWidget(self.thenby_label)
        self.subgroup_combo = QComboBox()
        self.subgroup_combo.addItem("None", "none")
        self.subgroup_combo.addItem("Category", "category")
        self.subgroup_combo.addItem("Type", "type")
        self.subgroup_combo.addItem("Tag", "tag")
        self.subgroup_combo.setCurrentIndex(self.subgroup_combo.findData("tag"))
        self.subgroup_combo.currentIndexChanged.connect(self.refresh_snippet_list)
        view_layout.addWidget(self.subgroup_combo)
        view_layout.addWidget(QLabel("Sort:"))
        self.sort_combo = QComboBox()
        self.sort_combo.addItem("Name", "name")
        self.sort_combo.addItem("Recently used", "recent")
        self.sort_combo.addItem("Most used", "used")
        self.sort_combo.currentIndexChanged.connect(self.refresh_snippet_list)
        view_layout.addWidget(self.sort_combo)
        view_layout.addWidget(QLabel("View:"))
        self.view_combo = QComboBox()
        self.view_combo.addItem("Tree", "tree")
        self.view_combo.addItem("Table", "table")
        self.view_combo.setCurrentIndex(self.view_combo.findData("table"))
        self.view_combo.currentIndexChanged.connect(self._on_view_changed)
        view_layout.addWidget(self.view_combo)
        left_layout.addLayout(view_layout)

        # Snippet tree (groupable list); font comes from the QSS stylesheet.
        self.snippet_list = SnippetTreeWidget()
        self.snippet_list.setHeaderHidden(True)
        self.snippet_list.setItemDelegate(SnippetTreeDelegate(self.snippet_list))
        self.snippet_list.setDragDropMode(QAbstractItemView.InternalMove)
        self.snippet_list.itemSelectionChanged.connect(self.on_selection_changed)
        left_layout.addWidget(self.snippet_list)

        # Snippet table: flat, sortable view of the same snippets.
        # Grouping does not apply here (Category is a column instead).
        self.snippet_table = SnippetTableWidget(0, 7)
        self.snippet_table.setObjectName("snippet_table")
        self.snippet_table.setHorizontalHeaderLabels(
            ["Trigger", "Type", "Category", "Tags", "Uses", "Last used", "Preview"]
        )
        table_header = self.snippet_table.horizontalHeader()
        table_header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        table_header.setStretchLastSection(True)
        self.snippet_table.setColumnWidth(0, 140)
        self.snippet_table.setColumnWidth(1, 90)
        self.snippet_table.setColumnWidth(2, 120)
        self.snippet_table.setColumnWidth(3, 140)
        self.snippet_table.setColumnWidth(4, 60)
        self.snippet_table.setColumnWidth(5, 130)
        self.snippet_table.verticalHeader().setVisible(False)
        self.snippet_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        # ExtendedSelection (item 4): bulk actions act on every selected
        # row. Single-selection behaviour is preserved for the tree,
        # where drag-and-drop and current-item logic assume one item.
        self.snippet_table.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection
        )
        self.snippet_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self.snippet_table.setSortingEnabled(True)
        # Qt defaults the sort indicator to column 0 descending; clear it
        # so the first fill keeps the sort-combo order until the user
        # actually clicks a header.
        table_header.setSortIndicator(-1, Qt.AscendingOrder)
        self.snippet_table.itemSelectionChanged.connect(self.on_selection_changed)
        self.snippet_table.itemDoubleClicked.connect(lambda *args: self.edit_snippet())
        # Column control (item 7): right-click the header to show/hide
        # columns; double-click a header boundary to fit its contents.
        table_header.setContextMenuPolicy(Qt.CustomContextMenu)
        table_header.customContextMenuRequested.connect(self._show_table_header_menu)
        table_header.sectionDoubleClicked.connect(self._fit_table_column)
        left_layout.addWidget(self.snippet_table)
        self.empty_state_label = QLabel("")
        self.empty_state_label.setWordWrap(True)
        self.empty_state_label.setAlignment(Qt.AlignCenter)
        left_layout.addWidget(self.empty_state_label)
        self.empty_state_btn = QPushButton("Import starter snippets")
        self.empty_state_btn.setToolTip("Add four starter snippets to get going")
        self.empty_state_btn.clicked.connect(self.import_starter_snippets)
        left_layout.addWidget(self.empty_state_btn)
        self._apply_snippet_view()

        # Add/Edit/Delete buttons (shortcuts shown inline in the labels so
        # they're visible without hovering; tooltips add the details)
        btn_layout = QHBoxLayout()
        self.add_btn = QPushButton("Add (Ctrl+N)")
        self.add_btn.setToolTip("Add a snippet")
        self.add_btn.clicked.connect(self.add_snippet)
        self.edit_btn = QPushButton("Edit (F2)")
        self.edit_btn.setToolTip("Edit the selected snippet (Enter also works)")
        self.edit_btn.clicked.connect(self.edit_snippet)
        self.delete_btn = QPushButton("Delete (Del)")
        self.delete_btn.setToolTip("Delete the selected snippet")
        self.delete_btn.clicked.connect(self.delete_snippet)
        self.shortcuts_btn = QPushButton("Shortcuts (?)")
        self.shortcuts_btn.setToolTip("Show keyboard shortcuts (Ctrl+F focuses search)")
        self.shortcuts_btn.clicked.connect(self.show_shortcuts)
        btn_layout.addWidget(self.add_btn)
        btn_layout.addWidget(self.edit_btn)
        btn_layout.addWidget(self.delete_btn)
        btn_layout.addWidget(self.shortcuts_btn)
        left_layout.addLayout(btn_layout)

        # Bulk-action row (item 4): table view only; hidden in tree view
        # by _apply_snippet_view().
        self.bulk_bar = QWidget()
        bulk_layout = QHBoxLayout(self.bulk_bar)
        bulk_layout.setContentsMargins(0, 0, 0, 0)
        bulk_layout.addWidget(QLabel("Bulk:"))
        self.bulk_enable_btn = QPushButton("Enable")
        self.bulk_enable_btn.clicked.connect(lambda: self._bulk_update("enable"))
        self.bulk_disable_btn = QPushButton("Disable")
        self.bulk_disable_btn.clicked.connect(lambda: self._bulk_update("disable"))
        self.bulk_favourite_btn = QPushButton("Favourite")
        self.bulk_favourite_btn.clicked.connect(lambda: self._bulk_update("favourite"))
        self.bulk_category_btn = QPushButton("Category\u2026")
        self.bulk_category_btn.clicked.connect(self.bulk_set_category)
        self.bulk_delete_btn = QPushButton("Delete")
        self.bulk_delete_btn.clicked.connect(self.bulk_delete)
        for _b in (
            self.bulk_enable_btn, self.bulk_disable_btn, self.bulk_favourite_btn,
            self.bulk_category_btn, self.bulk_delete_btn,
        ):
            bulk_layout.addWidget(_b)
        bulk_layout.addStretch(1)
        left_layout.addWidget(self.bulk_bar)

        # Keyboard shortcuts. Add is window-wide; Edit/Delete are scoped to
        # the tree so typing in the search box (or settings fields) never
        # triggers them.
        self.add_shortcut = QShortcut(QKeySequence("Ctrl+N"), self)
        self.add_shortcut.activated.connect(self.add_snippet)
        for seq in (QKeySequence("F2"), QKeySequence(Qt.Key_Return), QKeySequence(Qt.Key_Enter)):
            shortcut = QShortcut(seq, self.snippet_list)
            shortcut.setContext(Qt.WidgetWithChildrenShortcut)
            shortcut.activated.connect(self.edit_snippet)
        self.delete_shortcut = QShortcut(QKeySequence(Qt.Key_Delete), self.snippet_list)
        self.delete_shortcut.setContext(Qt.WidgetWithChildrenShortcut)
        self.delete_shortcut.activated.connect(self.delete_snippet)
        # Same edit/delete shortcuts, scoped to the table so they work
        # when the table (not the tree) has focus.
        for seq in (QKeySequence("F2"), QKeySequence(Qt.Key_Return), QKeySequence(Qt.Key_Enter)):
            shortcut = QShortcut(seq, self.snippet_table)
            shortcut.setContext(Qt.WidgetWithChildrenShortcut)
            shortcut.activated.connect(self.edit_snippet)
        self.delete_shortcut_table = QShortcut(QKeySequence(Qt.Key_Delete), self.snippet_table)
        self.delete_shortcut_table.setContext(Qt.WidgetWithChildrenShortcut)
        self.delete_shortcut_table.activated.connect(self.delete_snippet)

        # Keyboard-first navigation (item 7). Ctrl+F focuses search from
        # anywhere in the manager; Esc clears it while it has focus;
        # Ctrl+1 / Ctrl+2 switch Tree / Table without the mouse.
        self.search_shortcut = QShortcut(QKeySequence("Ctrl+F"), self)
        self.search_shortcut.setContext(Qt.WindowShortcut)
        self.search_shortcut.activated.connect(self._focus_search)
        self.clear_search_shortcut = QShortcut(
            QKeySequence(Qt.Key_Escape), self.search_input
        )
        self.clear_search_shortcut.setContext(Qt.WidgetShortcut)
        self.clear_search_shortcut.activated.connect(self.search_input.clear)
        for _seq, _view in (("Ctrl+1", "tree"), ("Ctrl+2", "table")):
            _sc = QShortcut(QKeySequence(_seq), self)
            _sc.setContext(Qt.WindowShortcut)
            _sc.activated.connect(lambda v=_view: self._set_view(v))
        # Undo (item 1) is scoped to the snippet views, like Edit/Delete:
        # a window-wide Ctrl+Z would steal text-field undo in the search
        # box, settings fields, and dialogs.
        for _widget in (self.snippet_list, self.snippet_table):
            _undo_sc = QShortcut(QKeySequence("Ctrl+Z"), _widget)
            _undo_sc.setContext(Qt.WidgetWithChildrenShortcut)
            _undo_sc.activated.connect(self.undo_last)

        main_layout.addWidget(left_group, 1)

        # Right panel: Details + Language selector
        right_group = QGroupBox("Settings")
        self.right_group = right_group
        right_layout = QVBoxLayout(right_group)

        # Language selector
        lang_layout = QHBoxLayout()
        lang_layout.addWidget(QLabel("Programming language:"))
        self.language_combo = QComboBox()
        self.language_combo.addItems(["python", "javascript", "java", "c++", "go", "rust"])
        self.language_combo.setCurrentText(self.daemon.current_language if self.daemon else "python")
        self.language_combo.currentTextChanged.connect(self.on_language_changed)
        lang_layout.addWidget(self.language_combo)
        right_layout.addLayout(lang_layout)

        # Preview area: structured details like the tree mockup, not one
        # block of text. Category / Tags / Type / Trigger as labelled
        # values, a Used line, the rendered expansion, and Copy/Insert.
        preview_layout = QVBoxLayout()
        detail_layout = QHBoxLayout()
        self.preview_detail_labels = {}
        for key, title in (("category", "Category"), ("tags", "Tags"),
                           ("type", "Type"), ("trigger", "Trigger")):
            column = QVBoxLayout()
            column.addWidget(QLabel(title))
            value = QLabel("\u2014")
            value.setWordWrap(True)
            if key == "trigger":
                trigger_font = value.font()
                trigger_font.setBold(True)
                value.setFont(trigger_font)
            column.addWidget(value)
            detail_layout.addLayout(column)
            self.preview_detail_labels[key] = value
        preview_layout.addLayout(detail_layout)
        self.preview_used_label = QLabel("Select a snippet to preview")
        preview_layout.addWidget(self.preview_used_label)
        self.preview_note_label = QLabel("")
        self.preview_note_label.setWordWrap(True)
        preview_layout.addWidget(self.preview_note_label)
        preview_layout.addWidget(QLabel("Expanded text:"))
        # QTextBrowser, not QTextEdit: only the browser emits
        # anchorClicked, which the clickable missing placeholders need.
        self.preview_text = QTextBrowser()
        self.preview_text.setReadOnly(True)
        self.preview_text.setOpenLinks(False)
        self.preview_text.anchorClicked.connect(self._fill_placeholder_from_preview)
        self.preview_text.setPlaceholderText("Select a template snippet to preview")
        preview_layout.addWidget(self.preview_text)
        # Missing-placeholder flag (item 5): shown under the preview
        # whenever the selected snippet uses a placeholder with no
        # built-in and no saved custom-setting value.
        self.preview_missing_label = QLabel("")
        self.preview_missing_label.setWordWrap(True)
        preview_layout.addWidget(self.preview_missing_label)
        preview_btn_layout = QHBoxLayout()
        self.preview_mode_btn = QPushButton("Show raw text")
        self.preview_mode_btn.setToolTip(
            "Toggle between the rendered expansion and the raw text, "
            "with {placeholders} highlighted"
        )
        self.preview_mode_btn.clicked.connect(self._toggle_preview_mode)
        preview_btn_layout.addWidget(self.preview_mode_btn)
        self.preview_copy_btn = QPushButton("Copy")
        self.preview_copy_btn.setToolTip("Copy the rendered expansion to the clipboard")
        self.preview_copy_btn.clicked.connect(self.copy_preview)
        self.preview_copy_btn.setEnabled(False)
        self.preview_insert_btn = QPushButton("Insert")
        self.preview_insert_btn.setToolTip("Hide the manager and insert the expansion into the focused app")
        self.preview_insert_btn.clicked.connect(self.insert_preview)
        self.preview_insert_btn.setEnabled(False)
        preview_btn_layout.addWidget(self.preview_copy_btn)
        preview_btn_layout.addWidget(self.preview_insert_btn)
        preview_layout.addLayout(preview_btn_layout)
        right_layout.addLayout(preview_layout)

        # Settings, in three separate sections:
        # "Your details" (Name/Email, the values for {name}/{email}),
        # "Preferences" (keyboard, shortcuts, formats, close behaviour,
        # start-on-login) and "Placeholder values" (the {company}-style
        # values, formerly lumped in as "custom settings"). One Save
        # Settings button covers all three; each section folds away
        # separately and its folded state is saved with the window state.

        # --- Your details ---
        details_group = QGroupBox("")
        self.details_group = details_group
        details_layout = QFormLayout(details_group)
        self.name_edit = QLineEdit(self.daemon.config.get("name", "") if (self.daemon and hasattr(self.daemon, "config")) else "")
        self.email_edit = QLineEdit(self.daemon.config.get("email", "") if (self.daemon and hasattr(self.daemon, "config")) else "")
        details_layout.addRow("Name:", self.name_edit)
        details_layout.addRow("Email:", self.email_edit)

        # --- Preferences ---
        prefs_group = QGroupBox("")
        self.prefs_group = prefs_group
        prefs_layout = QFormLayout(prefs_group)
        # Keyboard layout for the evdev Wayland input listener (used when the
        # session is native Wayland; ignored under X11).
        self.layout_combo = QComboBox()
        current_layout = self.daemon.config.get("layout", "us") if (self.daemon and hasattr(self.daemon, "config")) else "us"
        for layout in ("us", "uk", "de", "fr", "es"):
            self.layout_combo.addItem(layout, layout)
        layout_index = self.layout_combo.findData(current_layout)
        self.layout_combo.setCurrentIndex(max(layout_index, 0))
        self.paste_hotkey_edit = QLineEdit(
            self.daemon.config.get("paste_hotkey", "ctrl+shift+v")
            if (self.daemon and hasattr(self.daemon, "config")) else "ctrl+shift+v"
        )
        self.paste_hotkey_edit.setPlaceholderText("ctrl+shift+v")
        self.date_format_edit = QLineEdit(
            self.daemon.config.get("date_format", "%Y-%m-%d")
            if (self.daemon and hasattr(self.daemon, "config")) else "%Y-%m-%d"
        )
        self.date_format_edit.setPlaceholderText("%Y-%m-%d (strftime)")
        self.time_format_edit = QLineEdit(
            self.daemon.config.get("time_format", "%H:%M")
            if (self.daemon and hasattr(self.daemon, "config")) else "%H:%M"
        )
        self.time_format_edit.setPlaceholderText("%H:%M (strftime)")
        prefs_layout.addRow("Keyboard layout:", self.layout_combo)
        prefs_layout.addRow("Paste shortcut:", self.paste_hotkey_edit)
        prefs_layout.addRow("Date format:", self.date_format_edit)
        prefs_layout.addRow("Time format:", self.time_format_edit)
        self.picker_hotkey_edit = QLineEdit(
            self.daemon.config.get("picker_hotkey", "ctrl+alt+space")
            if (self.daemon and hasattr(self.daemon, "config")) else "ctrl+alt+space"
        )
        self.picker_hotkey_edit.setPlaceholderText("ctrl+alt+space")
        self.picker_hotkey_edit.setToolTip(
            "Global shortcut for Quick Insert. Applies when you click Save "
            "Settings (X11/pynput only; also takes effect on next start)."
        )
        prefs_layout.addRow("Quick Insert shortcut:", self.picker_hotkey_edit)
        self.close_behavior_combo = QComboBox()
        self.close_behavior_combo.addItem("Quit Textr", "quit")
        self.close_behavior_combo.addItem("Minimize to tray", "tray")
        _close_idx = self.close_behavior_combo.findData(
            self.daemon.config.get("close_behavior", "quit")
            if (self.daemon and hasattr(self.daemon, "config")) else "quit"
        )
        self.close_behavior_combo.setCurrentIndex(max(_close_idx, 0))
        prefs_layout.addRow("Close window:", self.close_behavior_combo)
        self.autostart_check = QCheckBox("Start Textr when I log in (Linux)")
        self.autostart_check.blockSignals(True)
        self.autostart_check.setChecked(self._autostart_enabled())
        self.autostart_check.blockSignals(False)
        self.autostart_check.toggled.connect(self._on_autostart_toggled)
        prefs_layout.addRow(self.autostart_check)
        self.density_combo = QComboBox()
        self.density_combo.addItem("Comfortable", "comfortable")
        self.density_combo.addItem("Compact", "compact")
        self.density_combo.setToolTip("Row density for the snippet list and table")
        self.density_combo.currentIndexChanged.connect(self._on_display_pref_changed)
        prefs_layout.addRow("Density:", self.density_combo)
        self.text_size_combo = QComboBox()
        self.text_size_combo.addItem("Small", "small")
        self.text_size_combo.addItem("Medium", "medium")
        self.text_size_combo.addItem("Large", "large")
        self.text_size_combo.setToolTip("Text size for the manager window")
        self.text_size_combo.currentIndexChanged.connect(self._on_display_pref_changed)
        prefs_layout.addRow("Text size:", self.text_size_combo)

        # --- Placeholder values ---
        values_group = QGroupBox("")
        self.values_group = values_group
        values_layout = QFormLayout(values_group)
        self.user_settings_table = QTableWidget(0, 3)
        self.user_settings_table.setHorizontalHeaderLabels(
            ["Placeholder", "Value", "Used by"]
        )
        header = self.user_settings_table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setStretchLastSection(True)
        self.user_settings_table.setColumnWidth(0, 150)
        self.user_settings_table.setColumnWidth(1, 220)
        self.user_settings_table.verticalHeader().setVisible(False)
        self.user_settings_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.user_settings_table.setSortingEnabled(True)
        self.user_settings_table.setMinimumHeight(140)
        self.user_settings_table.itemChanged.connect(self._update_settings_dirty)
        values_layout.addRow(self.user_settings_table)

        custom_buttons = QHBoxLayout()
        self.add_setting_btn = QPushButton("Add value")
        self.add_setting_btn.clicked.connect(self.add_user_setting)
        self.remove_setting_btn = QPushButton("Remove selected")
        self.remove_setting_btn.clicked.connect(self.remove_user_settings)
        custom_buttons.addWidget(self.add_setting_btn)
        custom_buttons.addWidget(self.remove_setting_btn)
        values_layout.addRow(custom_buttons)

        self.find_missing_settings_btn = QPushButton("Find missing placeholders")
        self.find_missing_settings_btn.clicked.connect(self.find_missing_user_settings)
        values_layout.addRow(self.find_missing_settings_btn)
        hint = QLabel(
            "Double-click Placeholder/Value cells to edit. The Used by column shows "
            "how many snippets use each placeholder and which ones. Missing "
            "{placeholder_name} placeholders are added automatically on startup and "
            "after imports. Fill in values and click Save Settings."
        )
        hint.setWordWrap(True)
        values_layout.addRow(hint)

        config = self.daemon.config if self.daemon else {}
        custom_settings = config.get("user_settings", {})
        if isinstance(custom_settings, dict):
            for key, value in custom_settings.items():
                self.add_user_setting_row(str(key), str(value))

        # One Save button for all three sections.
        self.save_settings_btn = QPushButton("Save Settings")
        self.save_settings_btn.clicked.connect(self.save_user_config)
        # Unsaved-changes tracking (item 3): any edit marks the button
        # dirty until Save Settings is clicked (see _update_settings_dirty).
        self.name_edit.textChanged.connect(self._update_settings_dirty)
        self.email_edit.textChanged.connect(self._update_settings_dirty)
        self.layout_combo.currentIndexChanged.connect(self._update_settings_dirty)
        self.paste_hotkey_edit.textChanged.connect(self._update_settings_dirty)
        self.date_format_edit.textChanged.connect(self._update_settings_dirty)
        self.time_format_edit.textChanged.connect(self._update_settings_dirty)
        self.picker_hotkey_edit.textChanged.connect(self._update_settings_dirty)
        self.close_behavior_combo.currentIndexChanged.connect(self._update_settings_dirty)

        # Section toggles (headers): one per section, folded state saved.
        self.details_toggle_btn = self._make_section_toggle("Your details", details_group)
        self.prefs_toggle_btn = self._make_section_toggle("Preferences", prefs_group)
        self.values_toggle_btn = self._make_section_toggle("Placeholder values", values_group)
        self._section_toggles = {
            "details": (self.details_toggle_btn, details_group, "Your details"),
            "preferences": (self.prefs_toggle_btn, prefs_group, "Preferences"),
            "values": (self.values_toggle_btn, values_group, "Placeholder values"),
        }
        self._values_manual_show = False
        right_layout.addWidget(self.details_toggle_btn)
        right_layout.addWidget(details_group)
        right_layout.addWidget(self.prefs_toggle_btn)
        right_layout.addWidget(prefs_group)
        right_layout.addWidget(self.values_toggle_btn)
        right_layout.addWidget(values_group)
        right_layout.addWidget(self.save_settings_btn)
        # Bottom stretch: when sections are folded, the spare space
        # in the fixed-height panel collects here instead of
        # stretching the preview box or the Placeholder values table.
        right_layout.addStretch(1)


        # Bottom controls, grouped by intent instead of two flat rows of
        # equal buttons (11 buttons, mixing toggles, exit, data, and tools):
        #
        # - Daemon / Human Typing are persistent states, so they live as
        #   clickable pills in the status bar, always visible.
        # - Quick Insert is the daily action, so it is the one primary
        #   button, on its own row.
        # - Import/Export (JSON and packs) sit behind one Library menu;
        #   Backups / Trash / Cleanup stay as secondary buttons.
        # - No Quit button: the window's X already quits (closeEvent ->
        #   quit_app, saving and stopping the daemon), and the tray menu
        #   has Quit too.

        # Status-bar toggles.
        self.toggle_daemon_btn = QPushButton("Daemon")
        self.toggle_daemon_btn.setObjectName("toggle_daemon_btn")
        self.toggle_daemon_btn.setCheckable(True)
        self.toggle_daemon_btn.setChecked(self.daemon.enabled if self.daemon else True)
        self.toggle_daemon_btn.toggled.connect(self.toggle_daemon)

        self.toggle_writing_btn = QPushButton("Human Typing")
        self.toggle_writing_btn.setObjectName("toggle_writing_btn")
        self.toggle_writing_btn.setCheckable(True)
        self.toggle_writing_btn.setChecked(self.daemon.writing_mode if self.daemon else False)
        self.toggle_writing_btn.toggled.connect(self.toggle_writing_mode)
        self._sync_toggle_text()
        self.statusBar().addPermanentWidget(self.toggle_daemon_btn)
        self.statusBar().addPermanentWidget(self.toggle_writing_btn)
        # Library health (item 5) and Undo (item 1) live in the status
        # bar, always visible without opening a dialog.
        self.health_label = QLabel("")
        self.health_label.setToolTip("Library totals and the last backup time")
        self.statusBar().addPermanentWidget(self.health_label)
        self.undo_btn = QPushButton("Undo")
        self.undo_btn.setToolTip("Nothing to undo")
        self.undo_btn.setEnabled(False)
        self.undo_btn.clicked.connect(self.undo_last)
        self.statusBar().addPermanentWidget(self.undo_btn)
        self._sync_undo_button()

        # Primary action row: Quick Insert + the Library menu.
        # Backups / Trash / Cleanup live inside the Library menu now
        # (declutter), instead of as three permanent buttons.
        primary_layout = QHBoxLayout()
        self.picker_btn = QPushButton("Quick Insert (Ctrl+Alt+Space)")
        self.picker_btn.setObjectName("primary_btn")
        self.picker_btn.clicked.connect(self.show_picker)
        primary_layout.addWidget(self.picker_btn)
        self.library_menu = QMenu(self)
        self.library_menu.addAction("Import JSON", self.import_json)
        self.library_menu.addAction("Export JSON", self.export_json)
        self.library_menu.addSeparator()
        self.library_menu.addAction("Import Pack", self.import_pack)
        self.library_menu.addAction("Export Pack", self.export_pack)
        self.library_menu.addAction("Import starter snippets", self.import_starter_snippets)
        self.library_menu.addSeparator()
        self.library_menu.addAction("Backups", self.show_backups)
        self.library_menu.addAction("Trash", self.show_trash)
        self.library_menu.addAction("Cleanup", self.show_cleanup)
        self.library_menu.addSeparator()
        self.library_menu.addAction("Backup now", self.backup_now)
        self.library_menu.addAction("Export visible to CSV", self.export_visible_csv)
        self.library_menu.addSeparator()
        self.library_menu.addAction(
            "New snippet from clipboard", self.new_snippet_from_clipboard
        )
        self.library_btn = QPushButton("Library")
        self.library_btn.setMenu(self.library_menu)
        primary_layout.addWidget(self.library_btn)
        right_layout.addLayout(primary_layout)

        # Snippets and Settings panels share the window 50/50
        # (left_group already has stretch 1, so this matches it).
        # The right panel lives in a scroll area (item 3): language,
        # preview, profile, and custom settings stacked in a fixed-height
        # panel otherwise overflow at smaller window heights.
        self.right_scroll = QScrollArea()
        self.right_scroll.setWidgetResizable(True)
        self.right_scroll.setFrameShape(QFrame.NoFrame)
        self.right_scroll.setWidget(right_group)
        main_layout.addWidget(self.right_scroll, 1)

        # System Tray Icon Setup
        self.really_quit = False
        self.tray_icon = QSystemTrayIcon(self)
        self.tray_icon.setIcon(create_status_icon("green" if (self.daemon and self.daemon.enabled) else "red"))
        
        # Tray context menu
        tray_menu = QMenu()
        self.toggle_action = QAction("Toggle Pause", self)
        self.toggle_action.triggered.connect(self.toggle_pause)
        tray_menu.addAction(self.toggle_action)
        
        show_action = QAction("Show Manager", self)
        show_action.triggered.connect(self.showNormal)
        tray_menu.addAction(show_action)

        self.tray_insert_menu = QMenu("Insert snippet")
        self.tray_insert_menu.aboutToShow.connect(self._refresh_tray_insert_menu)
        tray_menu.addMenu(self.tray_insert_menu)

        picker_action = QAction("Quick Insert", self)
        picker_action.triggered.connect(self.show_picker)
        tray_menu.addAction(picker_action)
        
        quit_action = QAction("Quit", self)
        quit_action.triggered.connect(self.quit_app)
        tray_menu.addAction(quit_action)
        
        self.tray_icon.setContextMenu(tray_menu)
        self.tray_icon.show()
        
        # Polling status timer
        self.status_timer = QTimer(self)
        self.status_timer.timeout.connect(self.poll_daemon_status)
        self.status_timer.start(500)
        self.last_enabled_state = self.daemon.enabled if self.daemon else True

        self._restore_window_state()
        self._apply_snippet_view()
        self.refresh_snippet_list()
        self._restore_selection()
        self.find_missing_user_settings(notify=False)
        self._settings_baseline = self._settings_snapshot()
        self._update_settings_dirty()
        self._sync_picker_button_label()

    def get_expansion_preview(self, snippet):
        if not self.daemon:
            return ""
        snippet_type = snippet.get("type", "variable")
        if snippet_type == "variable":
            # Rendered, not raw: placeholders ({name}, custom settings,
            # relative dates) resolve here, as they do for templates,
            # so the preview (and the table Preview column) shows what
            # will actually expand. Missing placeholders render
            # literally, as in render_template without a prompt handler.
            raw = snippet.get("value", "")
            try:
                return self.daemon.render_template(raw)
            except Exception:
                return raw
        elif snippet_type == "template":
            current_lang = self.language_combo.currentText()
            raw = snippet.get("languages", {}).get(current_lang)
            if not raw:
                return f"(No template for {current_lang})"
            return self.daemon.render_template(raw)
        elif snippet_type == "command":
            return f"Command: {snippet.get('action')}"
        return ""

    def _is_table_view(self):
        """True when the Table view (not the Tree view) is active."""
        view = getattr(self, "view_combo", None)
        return view is not None and view.currentData() == "table"

    def _apply_snippet_view(self):
        """Show the active snippet view; grouping only applies to the tree."""
        is_table = self._is_table_view()
        self.snippet_list.setVisible(not is_table)
        self.snippet_table.setVisible(is_table)
        # Then-by only applies to the tree; hide it in Table view.
        self.subgroup_combo.setVisible(not is_table)
        if hasattr(self, "thenby_label"):
            self.thenby_label.setVisible(not is_table)
        self.group_combo.setEnabled(not is_table)
        # Bulk actions are contextual (declutter): only with 2+ rows
        # selected in the table, never as a permanent row.
        self._sync_bulk_bar()
        self.subgroup_combo.setEnabled(
            not is_table and self.group_combo.currentData() != "none"
        )

    def _sync_bulk_bar(self):
        """Show the bulk row only for a multi-selection in Table view."""
        if not hasattr(self, "bulk_bar"):
            return
        visible = False
        if self._is_table_view() and hasattr(self, "selected_triggers"):
            try:
                visible = len(self.selected_triggers()) > 1
            except Exception:
                visible = False
        self.bulk_bar.setVisible(visible)

    def _on_view_changed(self):
        """View switcher: show the chosen view and rebuild it."""
        self._apply_snippet_view()
        self.refresh_snippet_list()

    # ----- UI improvements: window state, filters, bulk, preview, shortcuts -----

    # ----- Undo (item 1) -----

    def _push_undo(self, label, trash_triggers=()):
        """Snapshot the snippet library before a mutation, for undo.

        The snapshot is a deep copy taken before the change, so undo
        restores exactly the pre-change library. `trash_triggers` names
        snippets a delete is about to move to Trash, so undo can also
        remove them from Trash (otherwise a restored snippet would sit
        in both places). The stack is capped at 20 entries.
        """
        if not self.daemon:
            return
        with self.daemon.snippets_lock:
            snapshot = deepcopy(dict(self.daemon.snippets))
        self._undo_stack.append((label, snapshot, list(trash_triggers)))
        del self._undo_stack[:-20]
        self._sync_undo_button()

    def _sync_undo_button(self):
        """Enable/disable the status-bar Undo button for the stack state."""
        if not hasattr(self, "undo_btn"):
            return
        self.undo_btn.setEnabled(bool(self._undo_stack))
        if self._undo_stack:
            self.undo_btn.setToolTip(f"Undo: {self._undo_stack[-1][0]} (Ctrl+Z in the list)")
        else:
            self.undo_btn.setToolTip("Nothing to undo")

    def undo_last(self):
        """Restore the most recent pre-change snapshot (Ctrl+Z / Undo)."""
        if not self._undo_stack or not self.daemon:
            return
        label, snapshot, trash_triggers = self._undo_stack.pop()
        with self.daemon.snippets_lock:
            self.daemon.snippets.clear()
            self.daemon.snippets.update(deepcopy(snapshot))
        self.daemon.save_snippets()
        for trigger in trash_triggers:
            if hasattr(self.daemon, "delete_trash_forever"):
                try:
                    self.daemon.delete_trash_forever(trigger)
                except Exception:
                    pass
        self.refresh_snippet_list()
        self._sync_undo_button()
        self.statusBar().showMessage(f"Undid: {label}", 3000)
        print(f"UI: Undid '{label}'")

    # ----- Autostart (item 2, Linux) -----

    def _autostart_file(self):
        """Path of the XDG autostart entry for Textr, or None."""
        autostart_dir = os.path.join(
            os.path.expanduser("~"), ".config", "autostart"
        )
        return os.path.join(autostart_dir, "textr.desktop")

    def _autostart_exec(self):
        """Command that launches this Textr the way it was started."""
        if getattr(sys, "frozen", False):
            return sys.executable
        argv0 = sys.argv[0] if sys.argv else ""
        if argv0 and not argv0.endswith(".py"):
            return argv0
        if argv0.endswith(".py"):
            return f"{sys.executable} {os.path.abspath(argv0)}"
        found = shutil.which("textr")
        return found or "textr"

    def _autostart_enabled(self):
        return os.path.exists(self._autostart_file())

    def _set_autostart(self, enabled):
        """Create/remove the autostart entry. Returns True on success."""
        path = self._autostart_file()
        try:
            if enabled:
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "w") as f:
                    f.write(
                        "[Desktop Entry]\n"
                        "Type=Application\n"
                        "Name=Textr\n"
                        f"Exec={self._autostart_exec()}\n"
                        "X-GNOME-Autostart-enabled=true\n"
                        "NoDisplay=false\n"
                    )
            elif os.path.exists(path):
                os.remove(path)
            return True
        except Exception as exc:
            print(f"Autostart change failed: {exc}")
            return False

    def _on_autostart_toggled(self, checked):
        if self._set_autostart(bool(checked)):
            self.statusBar().showMessage(
                "Textr will start on login" if checked else "Textr will not start on login",
                3000,
            )
        else:
            # Revert the checkbox: the file change did not happen.
            self.autostart_check.blockSignals(True)
            self.autostart_check.setChecked(not checked)
            self.autostart_check.blockSignals(False)
            QMessageBox.warning(
                self, "Start on login",
                "Could not update the autostart entry. Check the permissions "
                "of ~/.config/autostart and try again.",
            )

    # ----- Picker shortcut label (item 2) -----

    def _picker_hotkey_display(self):
        """'ctrl+alt+space' -> 'Ctrl+Alt+Space' for button labels."""
        combo = ""
        if self.daemon:
            combo = self.daemon.config.get("picker_hotkey", "ctrl+alt+space") or "ctrl+alt+space"
        return "+".join(part.capitalize() for part in combo.split("+"))

    def _sync_picker_button_label(self):
        if hasattr(self, "picker_btn"):
            self.picker_btn.setText(f"Quick Insert ({self._picker_hotkey_display()})")

    def _make_section_toggle(self, title, group):
        """Fold/unfold header button for one settings section."""
        btn = QPushButton("\u25be " + title)
        btn.setCheckable(True)
        btn.setChecked(True)
        btn.setToolTip(f"Show or hide {title}")
        btn.toggled.connect(
            lambda checked, b=btn, g=group, t=title: self._on_section_toggle(b, g, t, checked)
        )
        return btn

    def _on_section_toggle(self, btn, group, title, checked):
        """Fold/unfold one settings section and refresh its header arrow."""
        arrow = "\u25be" if checked else "\u25b8"
        btn.setText(f"{arrow} {title}")
        if group is getattr(self, "values_group", None):
            if self.selected_trigger() is None:
                self._values_manual_show = bool(checked)
            self._sync_values_section_visibility()
        else:
            group.setVisible(bool(checked))
        self._sync_right_content_height()

    def _sync_values_section_visibility(self):
        """Placeholder values is snippet settings: show it only when a snippet is selected.

        Your details and Preferences are global and keep their own fold
        state. The header stays visible with no selection, so the section
        stays findable, and unfolding it with nothing selected opens it
        anyway (a manual override, cleared on the next selection). A saved
        folded state still wins once a snippet is selected.
        """
        group = getattr(self, "values_group", None)
        btn = getattr(self, "values_toggle_btn", None)
        if group is None or btn is None:
            return
        has_selection = self.selected_trigger() is not None
        if has_selection:
            self._values_manual_show = False
        btn.setVisible(True)
        manual = getattr(self, "_values_manual_show", False)
        group.setVisible(btn.isChecked() and (has_selection or manual))

    def _sync_display_pref_combos(self):
        """Set the Density / Text size combos from the restored values."""
        for _combo, _value in (
            (getattr(self, "density_combo", None), getattr(self, "_density", "comfortable")),
            (getattr(self, "text_size_combo", None), getattr(self, "_text_size", "medium")),
        ):
            if _combo is None:
                continue
            _idx = _combo.findData(_value)
            if _idx >= 0:
                _combo.blockSignals(True)
                _combo.setCurrentIndex(_idx)
                _combo.blockSignals(False)

    def _on_display_pref_changed(self):
        """Density / text size changed: apply at once and persist."""
        if hasattr(self, "density_combo"):
            self._density = self.density_combo.currentData() or "comfortable"
        if hasattr(self, "text_size_combo"):
            self._text_size = self.text_size_combo.currentData() or "medium"
        self._apply_display_prefs()
        try:
            self._app_settings.setValue("density", self._density)
            self._app_settings.setValue("textSize", self._text_size)
        except Exception:
            pass

    def _apply_display_prefs(self):
        """Apply density (row heights) and text size (window stylesheet).

        Text size goes through a window-level stylesheet because the app
        stylesheet fixes QWidget font-size in px, which an application
        font change cannot override. Density sets table row heights
        directly; tree rows read _density in _add_snippet_item.
        """
        compact = getattr(self, "_density", "comfortable") == "compact"
        row_height = 22 if compact else 30
        table = getattr(self, "snippet_table", None)
        if table is not None:
            try:
                table.verticalHeader().setDefaultSectionSize(row_height)
                for _r in range(table.rowCount()):
                    table.setRowHeight(_r, row_height)
            except Exception:
                pass
        # Preview height lock: the Expanded-text box keeps its natural
        # sizeHint height in every state, instead of stretching to fill
        # freed space when a settings section is folded (its Expanding
        # vertical policy let it grow). setFixedHeight is re-applied
        # here so it survives refreshes and follows text-size changes.
        preview = getattr(self, "preview_text", None)
        if preview is not None:
            try:
                preview.setFixedHeight(preview.sizeHint().height())
            except Exception:
                pass
        px = {"small": 12, "medium": 14, "large": 17}.get(
            getattr(self, "_text_size", "medium"), 14
        )
        try:
            self.setStyleSheet(f"QWidget {{ font-size: {px}px; }}")
        except Exception:
            pass
        self._sync_right_content_height()

    def _all_expanded_content_height(self):
        """Height of the Settings panel with all three sections expanded.

        The layout's totalSizeHint excludes hidden groups, so a folded
        group's sizeHint (still valid while hidden, as the probe in
        testing showed) is added back with one spacing, giving the
        all-expanded height in any fold state and at any window width.
        """
        group = getattr(self, "right_group", None)
        layout = group.layout() if group is not None else None
        if layout is None:
            return 0
        height = layout.totalSizeHint().height()
        spacing = layout.spacing()
        for section in (
            getattr(self, "details_group", None),
            getattr(self, "prefs_group", None),
            getattr(self, "values_group", None),
        ):
            if section is not None and section.isHidden():
                height += section.sizeHint().height() + spacing
        return height

    def _sync_right_content_height(self):
        """Keep the containing area at its all-expanded height.

        The Settings panel gets a minimum height equal to the
        all-expanded content height, so folding a section never
        shrinks the panel or stretches the widgets inside it. The
        minimum only ever prevents shrinking: taller content (more
        placeholder rows, a narrower window) still grows the panel
        and scrolls as before. Recomputed on every sync so width
        changes (word wrap) are picked up.
        """
        group = getattr(self, "right_group", None)
        if group is None:
            return
        height = self._all_expanded_content_height()
        if height > 0:
            self._right_min_height = height
            group.setMinimumHeight(height)

    def _focus_search(self):
        """Ctrl+F: focus the search box and select its contents."""
        if hasattr(self, "search_input"):
            self.search_input.setFocus()
            self.search_input.selectAll()

    def _set_view(self, view):
        """Switch Tree/Table from a keyboard shortcut (Ctrl+1 / Ctrl+2)."""
        idx = self.view_combo.findData(view)
        if idx >= 0:
            self.view_combo.setCurrentIndex(idx)

    def show_shortcuts(self):
        """Keyboard-shortcut help dialog (item 7)."""
        dialog = QDialog(self)
        dialog.setWindowTitle("Keyboard shortcuts")
        dialog.setMinimumWidth(420)
        layout = QVBoxLayout(dialog)
        lines = [
            "Ctrl+N — Add snippet",
            "F2 / Enter — Edit selected snippet",
            "Delete — Delete selected snippet (to Trash)",
            "Ctrl+Z — Undo last change (while the snippet list has focus)",
            "Ctrl+F — Focus search",
            "Esc — Clear search (while search has focus)",
            "Ctrl+1 / Ctrl+2 — Tree view / Table view",
            f"{self._picker_hotkey_display()} — Quick Insert (anywhere)",
            "",
            "Closing the manager window quits Textr; the tray menu also has Quit.",
        ]
        label = QLabel("\n".join(lines))
        label.setWordWrap(True)
        layout.addWidget(label)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(dialog.accept)
        layout.addWidget(close_btn)
        dialog.exec()

    def _search_query(self):
        """Current search text, lowercased, or '' if the box is not built yet."""
        if hasattr(self, "search_input"):
            return self.search_input.text().strip().lower()
        return ""

    def _set_quick_filter(self, key):
        """Apply a quick-filter chip; clicking the active chip clears to All."""
        if key == self._quick_filter and key != "all":
            key = "all"
        self._quick_filter = key
        for _k, _btn in getattr(self, "_filter_buttons", {}).items():
            _btn.setChecked(_k == key)
        self.refresh_snippet_list()

    def _matches_quick_filter(self, trigger, snippet):
        """True if a snippet passes the active quick-filter chip."""
        mode = getattr(self, "_quick_filter", "all")
        if mode == "all":
            return True
        if mode == "favourites":
            return bool(snippet.get("favorite"))
        if mode in ("variable", "template", "command"):
            return snippet.get("type", "variable") == mode
        if mode == "disabled":
            return not snippet.get("enabled", True)
        if mode == "unused":
            return int(snippet.get("usage", 0) or 0) == 0
        return True

    def _update_filter_status(self, shown, total):
        """Result count + empty-state label for the current search/filter."""
        if hasattr(self, "filter_count_label"):
            if total == 0:
                self.filter_count_label.setText("No snippets")
            elif shown == total:
                self.filter_count_label.setText(f"{total} snippet(s)")
            else:
                self.filter_count_label.setText(f"Showing {shown} of {total} snippet(s)")
        if hasattr(self, "empty_state_label"):
            if shown == 0:
                if self._search_query() or getattr(self, "_quick_filter", "all") != "all":
                    self.empty_state_label.setText(
                        "No snippets match the current search or filters.\nClear the search or choose All."
                    )
                else:
                    self.empty_state_label.setText(
                        "No snippets yet.\nAdd your first snippet with Add (Ctrl+N)."
                    )
                self.empty_state_label.setVisible(True)
            else:
                self.empty_state_label.setVisible(False)
        if hasattr(self, "empty_state_btn"):
            # Starter import is only offered for a genuinely empty library:
            # no snippets in the "all" category view, no search, All filter.
            _empty_library = (
                total == 0
                and not self._search_query()
                and getattr(self, "_quick_filter", "all") == "all"
                and getattr(self, "category_combo", None) is not None
                and self.category_combo.currentText() == "all"
            )
            self.empty_state_btn.setVisible(bool(_empty_library))

    def _save_window_state(self):
        """Persist geometry, view/group/sort, and column widths (item 1)."""
        settings = self._app_settings
        settings.setValue("geometry", self.saveGeometry())
        settings.setValue("view", self.view_combo.currentData())
        settings.setValue("group", self.group_combo.currentData())
        settings.setValue("subgroup", self.subgroup_combo.currentData())
        settings.setValue("sort", self.sort_combo.currentData())
        settings.setValue("density", getattr(self, "_density", "comfortable"))
        settings.setValue("textSize", getattr(self, "_text_size", "medium"))
        _selected = self.selected_trigger()
        settings.setValue("selectedTrigger", _selected or "")
        settings.setValue(
            "tableColumnWidths",
            [self.snippet_table.columnWidth(c) for c in range(self.snippet_table.columnCount())],
        )
        settings.setValue(
            "settingsColumnWidths",
            [self.user_settings_table.columnWidth(c) for c in range(self.user_settings_table.columnCount())],
        )
        for _key, (_btn, _group, _title) in getattr(
            self, "_section_toggles", {}
        ).items():
            settings.setValue(
                f"section{_key.capitalize()}Expanded", bool(_btn.isChecked())
            )
        settings.setValue(
            "tableHiddenColumns",
            [c for c in range(self.snippet_table.columnCount())
             if self.snippet_table.isColumnHidden(c)],
        )
        settings.sync()

    def _restore_window_state(self):
        """Restore the persisted window state, falling back to defaults."""
        settings = self._app_settings
        geometry = settings.value("geometry")
        if geometry is not None:
            try:
                self.restoreGeometry(geometry)
            except Exception:
                pass
        for _attr, _key in (
            ("view_combo", "view"),
            ("group_combo", "group"),
            ("subgroup_combo", "subgroup"),
            ("sort_combo", "sort"),
        ):
            combo = getattr(self, _attr, None)
            data = settings.value(_key)
            if combo is not None and data is not None:
                idx = combo.findData(data)
                if idx < 0:
                    idx = combo.findData(str(data))
                if idx >= 0:
                    combo.blockSignals(True)
                    combo.setCurrentIndex(idx)
                    combo.blockSignals(False)
        _density = settings.value("density")
        if _density in ("comfortable", "compact"):
            self._density = _density
        _text_size = settings.value("textSize")
        if _text_size in ("small", "medium", "large"):
            self._text_size = _text_size
        self._sync_display_pref_combos()
        self._apply_display_prefs()
        _legacy_expanded = settings.value("settingsExpanded")
        for _key, (_btn, _group, _title) in getattr(
            self, "_section_toggles", {}
        ).items():
            _value = settings.value(f"section{_key.capitalize()}Expanded")
            if _value is None:
                _value = _legacy_expanded
            if _value is None:
                continue
            _checked = (
                bool(_value)
                if isinstance(_value, bool)
                else str(_value).lower() == "true"
            )
            _btn.blockSignals(True)
            _btn.setChecked(_checked)
            _btn.blockSignals(False)
            _group.setVisible(_checked)
            _arrow = "\u25be" if _checked else "\u25b8"
            _btn.setText(f"{_arrow} {_title}")
        for _table, _key in (
            (getattr(self, "snippet_table", None), "tableColumnWidths"),
            (getattr(self, "user_settings_table", None), "settingsColumnWidths"),
        ):
            if _table is None:
                continue
            widths = settings.value(_key)
            if isinstance(widths, (list, tuple)) and len(widths) == _table.columnCount():
                for _c, _w in enumerate(widths):
                    try:
                        _w = int(_w)
                    except (TypeError, ValueError):
                        continue
                    if _w > 0:
                        _table.setColumnWidth(_c, _w)
        hidden = settings.value("tableHiddenColumns")
        if isinstance(hidden, (list, tuple)):
            for _c in hidden:
                try:
                    _c = int(_c)
                except (TypeError, ValueError):
                    continue
                if 0 <= _c < self.snippet_table.columnCount():
                    self.snippet_table.setColumnHidden(_c, True)

        self._sync_values_section_visibility()
    def _restore_selection(self):
        """Re-select the snippet selected when the window last closed.

        Only when it still exists and the current category filter would
        show it; otherwise the no-selection state stands.
        """
        settings = getattr(self, "_app_settings", None)
        if settings is None or not self.daemon:
            return
        trigger = settings.value("selectedTrigger")
        if not trigger or trigger not in self.daemon.snippets:
            return
        snippet = self.daemon.snippets.get(trigger, {}) or {}
        if self.category_combo.currentText() not in (
            "all",
            snippet.get("category", "uncategorized"),
        ):
            return
        if self._is_table_view() and hasattr(self, "snippet_table"):
            for _r in range(self.snippet_table.rowCount()):
                _item = self.snippet_table.item(_r, 0)
                if _item is not None and _item.data(Qt.UserRole + 1) == trigger:
                    self.snippet_table.setCurrentCell(_r, 0)
                    break
        elif hasattr(self, "snippet_list"):
            _item = self.snippet_list.find_item(trigger)
            if _item is not None:
                self.snippet_list.setCurrentItem(_item)
        self.on_selection_changed()

    def _settings_snapshot(self):
        """Raw settings-field values, for unsaved-change detection (item 3)."""
        rows = []
        table = getattr(self, "user_settings_table", None)
        if table is not None:
            for _r in range(table.rowCount()):
                _k = table.item(_r, 0)
                _v = table.item(_r, 1)
                rows.append((_k.text() if _k else "", _v.text() if _v else ""))
        layout_data = self.layout_combo.currentData() if hasattr(self, "layout_combo") else None
        return (
            self.name_edit.text() if hasattr(self, "name_edit") else "",
            self.email_edit.text() if hasattr(self, "email_edit") else "",
            layout_data,
            self.paste_hotkey_edit.text() if hasattr(self, "paste_hotkey_edit") else "",
            self.picker_hotkey_edit.text() if hasattr(self, "picker_hotkey_edit") else "",
            self.close_behavior_combo.currentData() if hasattr(self, "close_behavior_combo") else None,
            self.date_format_edit.text() if hasattr(self, "date_format_edit") else "",
            self.time_format_edit.text() if hasattr(self, "time_format_edit") else "",
            tuple(rows),
        )

    def _update_settings_dirty(self, *args):
        """Mark Save Settings dirty (with a bullet) until it is saved."""
        if getattr(self, "_settings_baseline", None) is None:
            return
        if not hasattr(self, "save_settings_btn"):
            return
        dirty = self._settings_snapshot() != self._settings_baseline
        self.save_settings_btn.setText("Save Settings \u2022" if dirty else "Save Settings")
        self.save_settings_btn.setObjectName("save_settings_dirty" if dirty else "")
        try:
            _style = self.save_settings_btn.style()
            _style.unpolish(self.save_settings_btn)
            _style.polish(self.save_settings_btn)
        except Exception:
            pass

    def selected_triggers(self):
        """Triggers of every selected snippet (table multi-select aware)."""
        if self._is_table_view() and hasattr(self, "snippet_table"):
            triggers = []
            seen = set()
            for _index in self.snippet_table.selectedIndexes():
                item = self.snippet_table.item(_index.row(), 0)
                if item is None:
                    continue
                trigger = item.data(Qt.UserRole + 1)
                if trigger is not None and trigger not in seen:
                    seen.add(trigger)
                    triggers.append(trigger)
            return triggers
        trigger = self.selected_trigger()
        return [trigger] if trigger else []

    def _bulk_update(self, action):
        """Enable / disable / favourite every selected snippet (item 4)."""
        triggers = self.selected_triggers()
        if not triggers or not self.daemon:
            return
        self._push_undo(f"Bulk {action} ({len(triggers)} snippet(s))")
        for trigger in triggers:
            snippet = dict(self.daemon.snippets.get(trigger, {}))
            if action == "enable":
                snippet["enabled"] = True
            elif action == "disable":
                snippet["enabled"] = False
            elif action == "favourite":
                snippet["favorite"] = True
            self.daemon.add_or_update_snippet(trigger, snippet)
        self.refresh_snippet_list()
        self.statusBar().showMessage(f"Updated {len(triggers)} snippet(s)", 3000)

    def bulk_set_category(self):
        """Move every selected snippet to one chosen category."""
        triggers = self.selected_triggers()
        if not triggers or not self.daemon:
            return
        categories = self.daemon.get_categories() if hasattr(self.daemon, "get_categories") else []
        category, ok = QInputDialog.getItem(
            self, "Bulk category", "Move selected snippets to category:",
            list(categories), 0, True,
        )
        if not ok or not str(category).strip():
            return
        self.move_selected_to_category(str(category).strip())

    def move_selected_to_category(self, category):
        """Move every selected snippet to `category` (one undo entry)."""
        triggers = self.selected_triggers()
        if not triggers or not self.daemon or not str(category).strip():
            return
        category = str(category).strip()
        self._push_undo(f"Move {len(triggers)} snippet(s) to '{category}'")
        for trigger in triggers:
            snippet = dict(self.daemon.snippets.get(trigger, {}))
            snippet["category"] = category
            self.daemon.add_or_update_snippet(trigger, snippet)
        self.refresh_snippet_list()
        self.statusBar().showMessage(
            f"Moved {len(triggers)} snippet(s) to '{category}'", 3000
        )

    def prompt_move_to_new_category(self):
        """Ask for a (possibly new, possibly nested) category, then move."""
        if not self.daemon:
            return
        categories = self.daemon.get_categories() if hasattr(self.daemon, "get_categories") else []
        category, ok = QInputDialog.getItem(
            self, "Move to category", "Move selected snippets to category:",
            list(categories), 0, True,
        )
        if ok and str(category).strip():
            self.move_selected_to_category(str(category).strip())

    def bulk_delete(self):
        """Delete every selected snippet (to Trash) after one confirmation."""
        triggers = self.selected_triggers()
        if not triggers or not self.daemon:
            return
        preview = ", ".join(repr(t) for t in triggers[:10])
        if len(triggers) > 10:
            preview += f", ... ({len(triggers)} total)"
        reply = QMessageBox.question(
            self, "Confirm",
            f"Delete {len(triggers)} snippet(s)?\n\n{preview}\n\nThey will be moved to Trash.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        self._push_undo(
            f"Delete {len(triggers)} snippet(s)", trash_triggers=list(triggers)
        )
        for trigger in triggers:
            self.daemon.delete_snippet(trigger)
        self.refresh_snippet_list()
        self.statusBar().showMessage(
            f"{len(triggers)} snippet(s) moved to Trash (restore from Trash)", 4000
        )

    def _raw_expansion(self, snippet):
        """Expansion text before placeholder rendering (item 5, raw mode)."""
        snippet_type = snippet.get("type", "variable")
        if snippet_type == "variable":
            return snippet.get("value", "")
        if snippet_type == "template":
            current_lang = self.language_combo.currentText() if hasattr(self, "language_combo") else "python"
            raw = (snippet.get("languages", {}) or {}).get(current_lang)
            if not raw:
                return f"(No template for {current_lang})"
            return raw
        return f"Command: {snippet.get('action')}"

    def _preview_missing_names(self, raw):
        """Placeholder names in `raw` with no built-in and no saved value."""
        if not self.daemon or not raw:
            return []
        names = re.findall(r"\{([A-Za-z_][A-Za-z0-9_]*)(?:[+-]\d+)?\}", raw)
        builtins = set(self.BUILTIN_PLACEHOLDERS) | {"cursor"}
        settings = {}
        config = getattr(self.daemon, "config", None)
        if isinstance(config, dict):
            settings = config.get("user_settings", {}) or {}
        return sorted(
            {n for n in names if n not in builtins and not str(settings.get(n, "")).strip()}
        )

    def _highlight_placeholders_html(self, text):
        """Raw text as HTML with {placeholders} highlighted (item 5).

        Present placeholders are orange; missing ones (no built-in, no
        saved value) are red. A <pre> wrapper preserves the raw
        whitespace of code templates.
        """
        missing = set(self._preview_missing_names(text))
        escaped = html.escape(text or "")

        def _wrap(match):
            name = match.group(1)
            # Choice placeholders (group 1 is None) are always
            # resolvable: orange, never the missing red. Missing
            # placeholders are links: clicking one in the raw preview
            # fills it in (item 7).
            color = "#ff9a3c" if name is None or name not in missing else "#f38ba8"
            inner = f'<b><font color="{color}">{html.escape(match.group(0))}</font></b>'
            if name is not None and name in missing:
                return f'<a href="fill:{name}">{inner}</a>'
            return inner

        body = re.sub(
            r"\{choice:[^}]+\}|\{([A-Za-z_][A-Za-z0-9_]*)(?:[+-]\d+)?\}",
            _wrap, escaped,
        )
        return f"<pre>{body}</pre>"

    def _toggle_preview_mode(self):
        """Flip the preview between rendered and raw text (item 5)."""
        self._preview_mode = "raw" if self._preview_mode == "rendered" else "rendered"
        self._update_preview_display()

    def _update_preview_display(self):
        """Render the stored preview in the active Raw/Rendered mode."""
        if not hasattr(self, "preview_text"):
            return
        if self._preview_mode == "raw":
            self.preview_text.setHtml(self._highlight_placeholders_html(self._preview_raw_text))
        else:
            self.preview_text.setPlainText(self._preview_rendered_text)
        missing = self._preview_missing_names(self._preview_raw_text)
        if missing and hasattr(self, "preview_missing_label"):
            _hint = ""
            if self._preview_mode != "raw":
                _hint = " Switch to raw text and click a red placeholder to fill it."
            self.preview_missing_label.setText(
                "Missing settings: " + ", ".join(f"{{{n}}}" for n in missing)
                + " - no value set; expansion will prompt for them." + _hint
            )
            self.preview_missing_label.setStyleSheet("color: #f38ba8;")
        elif hasattr(self, "preview_missing_label"):
            self.preview_missing_label.setText("")
            self.preview_missing_label.setStyleSheet("")
        if hasattr(self, "preview_mode_btn"):
            self.preview_mode_btn.setText(
                "Show rendered" if self._preview_mode == "raw" else "Show raw text"
            )

    def _fill_placeholder_from_preview(self, url):
        """Anchor click in the raw preview: fill one missing placeholder."""
        name = url.toString()
        if name.startswith("fill:"):
            self._fill_placeholder_value(name[len("fill:"):])

    def _fill_placeholder_value(self, name):
        """Prompt for one placeholder value and save it as a setting."""
        if not name or not self.daemon or not hasattr(self, "user_settings_table"):
            return
        current = ""
        row = None
        for _r in range(self.user_settings_table.rowCount()):
            _item = self.user_settings_table.item(_r, 0)
            if _item is not None and _item.text().strip() == name:
                row = _r
                _val = self.user_settings_table.item(_r, 1)
                current = _val.text() if _val else ""
                break
        value, ok = QInputDialog.getText(
            self, "Fill placeholder", f"Value for {{{name}}}:", QLineEdit.Normal, current
        )
        if not ok:
            return
        if row is None:
            row = self.add_user_setting_row(name, value)
        else:
            self.user_settings_table.setItem(row, 1, QTableWidgetItem(value))
        self.save_user_config()
        self.refresh_user_setting_usage()

    # ----- Library health, backup, columns, CSV, categories/tags -----

    def _update_health_status(self):
        """Status-bar totals + last backup time (item 5)."""
        if not hasattr(self, "health_label") or not self.daemon:
            return
        with self.daemon.snippets_lock:
            snippets = dict(self.daemon.snippets)
        total = len(snippets)
        unused = sum(1 for sn in snippets.values() if int(sn.get("usage", 0) or 0) == 0)
        disabled = sum(1 for sn in snippets.values() if not sn.get("enabled", True))
        duplicates = 0
        if "_normalized_expansion" in globals():
            seen = {}
            for sn in snippets.values():
                key = _normalized_expansion(sn)
                seen[key] = seen.get(key, 0) + 1
            duplicates = sum(count - 1 for count in seen.values() if count > 1)
        last_backup = "never"
        list_backups = getattr(self.daemon, "list_backups", None)
        if callable(list_backups):
            try:
                backup_times = [
                    b["mtime"] for b in list_backups() if b.get("kind") == "snippets"
                ]
                if backup_times:
                    import datetime as _dt
                    when = _dt.datetime.fromtimestamp(max(backup_times))
                    today = _dt.datetime.now().date()
                    if when.date() == today:
                        last_backup = "today " + when.strftime("%H:%M")
                    else:
                        last_backup = when.strftime("%Y-%m-%d")
            except Exception:
                pass
        parts = [f"{total} snippet(s)", f"{unused} never used", f"{disabled} disabled"]
        if duplicates:
            parts.append(f"{duplicates} duplicate(s)")
        parts.append(f"Last backup: {last_backup}")
        self.health_label.setText("  \u2022  ".join(parts))

    def backup_now(self):
        """Back up snippets + config immediately (Library menu, item 5)."""
        if not self.daemon:
            return
        backup = getattr(self.daemon, "backup_now", None)
        paths = backup() if callable(backup) else []
        self._update_health_status()
        if paths:
            self.statusBar().showMessage(
                f"Backed up {len(paths)} file(s)", 3000
            )
        else:
            self.statusBar().showMessage("Nothing to back up yet", 3000)

    def _show_table_header_menu(self, pos):
        """Right-click header menu: show/hide table columns (item 7)."""
        table = self.snippet_table
        header = table.horizontalHeader()
        menu = QMenu(header)
        for col in range(table.columnCount()):
            header_item = table.horizontalHeaderItem(col)
            label = header_item.text() if header_item is not None else f"Column {col}"
            action = menu.addAction(label)
            action.setCheckable(True)
            action.setChecked(not table.isColumnHidden(col))
            if col == 0:
                # Trigger is the identity column; it stays visible.
                action.setEnabled(False)
            action.triggered.connect(
                lambda checked, c=col: table.setColumnHidden(c, not checked)
            )
        menu.exec(header.mapToGlobal(pos))

    def _fit_table_column(self, section):
        """Double-click a header boundary: fit that column to contents."""
        self.snippet_table.resizeColumnToContents(section)

    def export_visible_csv(self):
        """Export the current search/filter result to CSV (item 7).

        Only visible (not hidden) columns are exported, in table order.
        The Preview column carries the full rendered expansion.
        """
        if not self.daemon:
            return
        file_path, _ = QFileDialog.getSaveFileName(
            self, "Export visible snippets", "", "CSV Files (*.csv)"
        )
        if not file_path:
            return
        table = self.snippet_table
        visible = [c for c in range(table.columnCount()) if not table.isColumnHidden(c)]
        headers = []
        for col in visible:
            header_item = table.horizontalHeaderItem(col)
            headers.append(header_item.text() if header_item is not None else f"Column {col}")
        try:
            with open(file_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(headers)
                for trigger, snippet in self._last_filtered_items:
                    uses = int(snippet.get("usage", 0) or 0)
                    values = {
                        "Trigger": trigger,
                        "Type": snippet.get("type", "variable"),
                        "Category": snippet.get("category", "uncategorized"),
                        "Tags": ", ".join(snippet.get("tags") or []),
                        "Uses": str(uses),
                        "Last used": snippet.get("last_used") or "never",
                        "Preview": " ".join(str(self.get_expansion_preview(snippet)).split()),
                    }
                    writer.writerow([values.get(h, "") for h in headers])
            self.statusBar().showMessage(
                f"Exported {len(self._last_filtered_items)} snippet(s) to CSV", 3000
            )
        except Exception as exc:
            QMessageBox.critical(self, "Export failed", f"Could not write CSV:\n{exc}")

    def category_counts(self):
        """[(category, snippet count)] sorted by name."""
        counts = {}
        if self.daemon:
            with self.daemon.snippets_lock:
                snippets = list(self.daemon.snippets.values())
            for snippet in snippets:
                category = snippet.get("category", "uncategorized") or "uncategorized"
                counts[category] = counts.get(category, 0) + 1
        return sorted(counts.items(), key=lambda kv: kv[0].lower())

    def tag_counts(self):
        """[(tag, snippet count)] sorted by name."""
        counts = {}
        if self.daemon:
            with self.daemon.snippets_lock:
                snippets = list(self.daemon.snippets.values())
            for snippet in snippets:
                for tag in snippet.get("tags") or []:
                    counts[tag] = counts.get(tag, 0) + 1
        return sorted(counts.items(), key=lambda kv: kv[0].lower())

    def rename_category(self, old, new):
        """Rename a category (and its nested children) everywhere."""
        if not self.daemon or not old or not new or old == new:
            return 0
        self._push_undo(f"Rename category '{old}' to '{new}'")
        count = 0
        with self.daemon.snippets_lock:
            for snippet in self.daemon.snippets.values():
                category = snippet.get("category", "uncategorized") or "uncategorized"
                if category == old:
                    snippet["category"] = new
                    count += 1
                elif category.startswith(old + "/"):
                    snippet["category"] = new + category[len(old):]
                    count += 1
        self.daemon.save_snippets()
        self.refresh_snippet_list()
        return count

    def delete_category(self, old):
        """Move a category (and nested children) to 'uncategorized'."""
        if not self.daemon or not old:
            return 0
        self._push_undo(f"Delete category '{old}'")
        count = 0
        with self.daemon.snippets_lock:
            for snippet in self.daemon.snippets.values():
                category = snippet.get("category", "uncategorized") or "uncategorized"
                if category == old or category.startswith(old + "/"):
                    snippet["category"] = "uncategorized"
                    count += 1
        self.daemon.save_snippets()
        self.refresh_snippet_list()
        return count

    def rename_tag(self, old, new):
        """Rename a tag on every snippet that has it (deduped)."""
        if not self.daemon or not old or not new or old == new:
            return 0
        self._push_undo(f"Rename tag '{old}' to '{new}'")
        count = 0
        with self.daemon.snippets_lock:
            for snippet in self.daemon.snippets.values():
                tags = snippet.get("tags") or []
                if old in tags:
                    renamed = [new if t == old else t for t in tags]
                    snippet["tags"] = list(dict.fromkeys(renamed))
                    count += 1
        self.daemon.save_snippets()
        self.refresh_snippet_list()
        return count

    def delete_tag(self, old):
        """Remove a tag from every snippet that has it."""
        if not self.daemon or not old:
            return 0
        self._push_undo(f"Delete tag '{old}'")
        count = 0
        with self.daemon.snippets_lock:
            for snippet in self.daemon.snippets.values():
                tags = snippet.get("tags") or []
                if old in tags:
                    snippet["tags"] = [t for t in tags if t != old]
                    count += 1
        self.daemon.save_snippets()
        self.refresh_snippet_list()
        return count

    def show_manage_categories(self):
        """Open the category/tag management dialog (item 4)."""
        if not self.daemon:
            return
        dialog = CategoryTagDialog(self)
        dialog.exec()
        self.refresh_snippet_list()

    def selected_trigger(self):
        """Trigger of the selected snippet in whichever view is active."""
        if self._is_table_view() and hasattr(self, "snippet_table"):
            row = self.snippet_table.currentRow()
            if row < 0:
                return None
            item = self.snippet_table.item(row, 0)
            return item.data(Qt.UserRole + 1) if item is not None else None
        selected = self.snippet_list.selectedItems()
        if not selected:
            return None
        return selected[0].data(0, Qt.UserRole)

    def _table_preview_text(self, snippet):
        """One-line preview for the table's Preview column."""
        preview = " ".join(str(self.get_expansion_preview(snippet)).split())
        return preview if len(preview) <= 80 else preview[:79] + "\u2026"

    def _refresh_snippet_table(self, items, selected_before):
        """Fill the table with (trigger, snippet) pairs, keeping sort/selection."""
        _query = self._search_query()
        _highlight_query = (
            _query
            if not (getattr(self, "_search_regex", False) or getattr(self, "_search_case_sensitive", False))
            else ""
        )
        table = self.snippet_table
        header = table.horizontalHeader()
        sort_section = header.sortIndicatorSection()
        sort_order = header.sortIndicatorOrder()
        table.setSortingEnabled(False)
        table.setRowCount(0)
        for trigger, snippet in items:
            row = table.rowCount()
            table.insertRow(row)
            snippet_type = snippet.get("type", "variable")
            category = snippet.get("category", "uncategorized")
            enabled = snippet.get("enabled", True)
            tags = snippet.get("tags") or []
            uses = int(snippet.get("usage", 0))
            last_used = snippet.get("last_used") or "never"
            state = " | DISABLED" if not enabled else ""
            preview = self._table_preview_text(snippet)
            cells = [
                (trigger, trigger.lower()),
                (snippet_type, snippet_type.lower()),
                (category, category.lower()),
                (", ".join(tags), ", ".join(tags).lower()),
                (str(uses), uses),
                (last_used, last_used if last_used != "never" else ""),
                (preview, preview.lower()),
            ]
            for col, (text, sort_key) in enumerate(cells):
                if col == 0 and snippet.get("favorite"):
                    text = f"\u2605 {text}"
                cell = SortableTableWidgetItem(text)
                # Search-match highlighting (item 2): matched cells are
                # bold with a subtle tint, so a match is visible at a
                # glance without losing the cell's own text.
                if _highlight_query and _highlight_query in str(text).lower():
                    _font = cell.font()
                    _font.setBold(True)
                    cell.setFont(_font)
                    cell.setBackground(QColor("#33241a"))
                cell.setData(Qt.ItemDataRole.UserRole, sort_key)
                if col == 0:
                    # Trigger lives in UserRole + 1: UserRole holds the sort key.
                    cell.setData(Qt.UserRole + 1, trigger)
                    cell.setForeground(
                        QColor(self.DISABLED_COLOR if not enabled else self.TYPE_COLORS.get(snippet_type, "#d6d6de"))
                    )
                    cell.setToolTip(f"{snippet_type.capitalize()} | Category: {category}{state}")
                table.setItem(row, col, cell)
        if sort_section < 0:
            # No header sort yet: clear the indicator before re-enabling
            # sorting, or Qt would sort by column 0 descending on enable.
            header.setSortIndicator(-1, Qt.AscendingOrder)
        table.setSortingEnabled(True)
        if sort_section >= 0:
            table.sortItems(sort_section, sort_order)
        if selected_before:
            for row in range(table.rowCount()):
                item = table.item(row, 0)
                if item is not None and item.data(Qt.UserRole + 1) == selected_before:
                    table.setCurrentCell(row, 0)
                    break

    def refresh_snippet_list(self):
        # Remember the selection so rebuilds (search, language switch, daemon
        # refresh) don't drop it - the user shouldn't have to re-click.
        selected_before = self.selected_trigger()

        self.snippet_list.clear()

        if not self.daemon:
            return

        categories = self.daemon.get_categories()
        current_cat = self.category_combo.currentText()
        self.category_combo.blockSignals(True)
        self.category_combo.clear()
        self.category_combo.addItems(["all"] + categories)
        if current_cat in categories or current_cat == "all":
            self.category_combo.setCurrentText(current_cat)
        self.category_combo.blockSignals(False)

        selected_category = self.category_combo.currentText()
        snippets = self.daemon.get_snippets_by_category(selected_category)

        query = self.search_input.text().strip()
        items = [
            (trigger, snippet)
            for trigger, snippet in snippets.items()
            if self._matches_search(trigger, snippet, query)
            and self._matches_quick_filter(trigger, snippet)
        ]
        self._sort_items(items)
        # The tree delegate reads this for search-match bolding (item 2).
        self.snippet_list._search_query = (
            query.lower()
            if query and not self._search_regex and not self._search_case_sensitive
            else ""
        )
        self._update_filter_status(len(items), len(snippets))
        if getattr(self, "_search_regex_error", None):
            self.statusBar().showMessage(
                f"Invalid regex ({self._search_regex_error}) \u2014 plain-text search used", 4000
            )
        self._last_filtered_items = list(items)
        self._update_health_status()

        if self._is_table_view():
            self._refresh_snippet_table(items, selected_before)
            self.refresh_user_setting_usage()
            self._sync_values_section_visibility()
            self._apply_display_prefs()
            return

        group_mode = self.group_combo.currentData()
        sub_mode = self.subgroup_combo.currentData()
        if group_mode == "none":
            for trigger, snippet in items:
                self._add_snippet_item(self.snippet_list, trigger, snippet)
        else:
            # Virtual sections first: the 3 most recently used snippets,
            # then favourited snippets. Both are views of `items` (already
            # search/category filtered), so a snippet also appears under
            # its regular group, as with tag grouping.
            recent = [
                (t, s) for t, s in items
                if (s.get("last_used") or "never") != "never"
            ]
            recent.sort(key=lambda kv: (kv[1].get("last_used") or "", kv[0].lower()), reverse=True)
            recent = recent[:3]
            if recent:
                parent = self._ensure_group(self.snippet_list, "Recent", virtual=True)
                for trigger, snippet in recent:
                    self._add_snippet_item(parent, trigger, snippet)
            favorites = [(t, s) for t, s in items if s.get("favorite")]
            if favorites:
                parent = self._ensure_group(self.snippet_list, "Favourites", virtual=True)
                for trigger, snippet in favorites:
                    self._add_snippet_item(parent, trigger, snippet)
            # Build groups first (sorted for stable display), then snippets.
            groups = {}
            for trigger, snippet in items:
                for path in self._group_paths(snippet, group_mode, sub_mode):
                    groups.setdefault(tuple(path), []).append((trigger, snippet))
            for path in sorted(groups, key=lambda p: tuple(part.lower() for part in p)):
                parent = self.snippet_list
                for part in path:
                    parent = self._ensure_group(parent, part)
                for trigger, snippet in groups[path]:
                    self._add_snippet_item(parent, trigger, snippet)

        self.snippet_list.expandAll()
        self._apply_group_counts()

        if selected_before:
            item = self.snippet_list.find_item(selected_before)
            if item is not None:
                self.snippet_list.setCurrentItem(item)
        self.refresh_user_setting_usage()
        self._sync_values_section_visibility()
        self._apply_display_prefs()

    def _matches_search(self, trigger, snippet, query):
        """True if the trigger, tags, note, or expansion text matches the query.

        Plain mode is a case-insensitive substring search (case-sensitive
        when the Aa toggle is on). Regex mode uses the compiled pattern
        from _get_search_pattern; an invalid pattern falls back to a plain
        substring search and _search_regex_error names the problem.
        """
        if not query:
            return True
        fields = [
            trigger,
            " ".join(snippet.get("tags") or []),
            str(snippet.get("note", "")),
        ]
        snippet_type = snippet.get("type", "variable")
        if snippet_type == "variable":
            fields.append(snippet.get("value", ""))
        elif snippet_type == "template":
            fields.append(" ".join(str(v) for v in snippet.get("languages", {}).values()))
        elif snippet_type == "command":
            fields.append(snippet.get("action", ""))
        pattern = self._get_search_pattern(query)
        if pattern is not None:
            return any(pattern.search(f or "") for f in fields)
        case_sensitive = getattr(self, "_search_case_sensitive", False)
        needle = query if case_sensitive else query.lower()
        for field in fields:
            haystack = field or ""
            if not case_sensitive:
                haystack = haystack.lower()
            if needle in haystack:
                return True
        return False

    def _get_search_pattern(self, query):
        """Compiled regex for the current search, or None for plain matching.

        The compile is cached per (query, regex, case) triple, so a refresh
        over the whole library compiles at most once. An invalid pattern
        leaves _search_regex_error set and matching falls back to plain.
        """
        key = (
            query,
            getattr(self, "_search_regex", False),
            getattr(self, "_search_case_sensitive", False),
        )
        if getattr(self, "_search_pattern_key", None) != key:
            self._search_pattern_key = key
            self._search_pattern_cache = None
            self._search_regex_error = None
            if key[1]:
                flags = 0 if key[2] else re.IGNORECASE
                try:
                    self._search_pattern_cache = re.compile(query, flags)
                except re.error as exc:
                    self._search_regex_error = str(exc)
        return self._search_pattern_cache

    def _on_search_mode_changed(self, checked=None):
        """Regex / case toggle flipped: re-run the search with the new mode."""
        self._search_regex = bool(
            getattr(self, "search_regex_btn", None) and self.search_regex_btn.isChecked()
        )
        self._search_case_sensitive = bool(
            getattr(self, "search_case_btn", None) and self.search_case_btn.isChecked()
        )
        self._search_regex_error = None
        self.refresh_snippet_list()

    def _sort_items(self, items):
        """Sort (trigger, snippet) pairs by the selected sort mode, in place."""
        mode = self.sort_combo.currentData()
        if mode == "recent":
            items.sort(
                key=lambda kv: (kv[1].get("last_used") or "0000-00-00T00:00:00", kv[0].lower()),
                reverse=True,
            )
        elif mode == "used":
            items.sort(key=lambda kv: (-int(kv[1].get("usage", 0)), kv[0].lower()))
        else:
            items.sort(key=lambda kv: kv[0].lower())

    def _snippet_tags(self, snippet):
        """Tags for a snippet, falling back to its legacy category."""
        tags = snippet.get("tags")
        if tags:
            return [t for t in tags if t]
        category = snippet.get("category", "")
        return [category] if category and category != "uncategorized" else []

    def _group_paths(self, snippet, group_mode, sub_mode="none"):
        """Group paths (each a list of parts) under which a snippet appears.

        ``sub_mode`` adds a second level ("then by") inside each primary group.
        """
        primary = self._primary_paths(snippet, group_mode)
        # Same mode twice (e.g. "tag then tag") is a degenerate choice: with
        # multiple tags the paths would cross-product. Treat it as one level.
        if sub_mode == "none" or sub_mode == group_mode:
            return primary
        subs = self._sub_paths(snippet, sub_mode)
        paths = []
        for path in primary:
            for sub in subs:
                if path[-len(sub):] == sub:
                    paths.append(path)  # redundant (e.g. category then category)
                else:
                    paths.append(path + sub)
        return paths

    def _primary_paths(self, snippet, group_mode):
        if group_mode == "type":
            return [[snippet.get("type", "variable")]]
        if group_mode == "tag":
            tags = list(dict.fromkeys(self._snippet_tags(snippet)))
            return [[t] for t in tags] if tags else [["uncategorized"]]
        # category: '/' separates nested folder levels
        category = snippet.get("category", "uncategorized") or "uncategorized"
        return [category.split("/")]

    def _sub_paths(self, snippet, sub_mode):
        """Secondary ("then by") group parts for a snippet."""
        if sub_mode == "type":
            return [[snippet.get("type", "variable")]]
        if sub_mode == "tag":
            tags = list(dict.fromkeys(self._snippet_tags(snippet)))
            return [[t] for t in tags] if tags else [["uncategorized"]]
        category = snippet.get("category", "uncategorized") or "uncategorized"
        return [category.split("/")]

    def _on_group_mode_changed(self):
        """The 'then by' selector only applies when a primary group is set."""
        self.subgroup_combo.setEnabled(self.group_combo.currentData() != "none")
        self.refresh_snippet_list()

    def _ensure_group(self, parent, name, virtual=False):
        """Find or create a (non-selectable) group node named `name`.

        `parent` may be the tree widget itself (root level) or another group
        item (nested folders). Virtual sections (Recent/Favourites) are
        marked in UserRole + 2 so drag-and-drop never treats them as a
        category to move a snippet into.
        """
        if isinstance(parent, QTreeWidget):
            count = parent.topLevelItemCount()
            get = parent.topLevelItem
        else:
            count = parent.childCount()
            get = parent.child
        for i in range(count):
            child = get(i)
            if child.data(0, Qt.UserRole + 1) == name:
                return child
        item = QTreeWidgetItem(parent, [name])
        item.setData(0, Qt.UserRole + 1, name)
        if virtual:
            item.setData(0, Qt.UserRole + 2, True)
        item.setFlags(item.flags() & ~Qt.ItemIsSelectable)
        # Bold orange headers (Daily Alvaro briefing) so groups stand out.
        item.setForeground(0, QColor("#ff9a3c"))
        group_font = item.font(0)
        group_font.setBold(True)
        item.setFont(0, group_font)
        return item

    def _count_snippet_leaves(self, item):
        """Snippet rows under a group, counting nested groups too."""
        total = 0
        for i in range(item.childCount()):
            child = item.child(i)
            if child.data(0, Qt.UserRole + 1) is not None:
                total += self._count_snippet_leaves(child)
            else:
                total += 1
        return total

    def _apply_group_counts(self):
        """Rewrite every group header as 'Name (n snippets)'.

        The name itself stays in UserRole + 1, so group lookup (which
        compares that data, not the text) is unaffected, and a rebuild
        starts from the plain name again.
        """
        def walk(parent):
            count = parent.topLevelItemCount() if isinstance(parent, QTreeWidget) else parent.childCount()
            get = parent.topLevelItem if isinstance(parent, QTreeWidget) else parent.child
            for i in range(count):
                child = get(i)
                name = child.data(0, Qt.UserRole + 1)
                if name is not None:
                    child.setText(0, f"{name} ({self._count_snippet_leaves(child)})")
                    walk(child)
        walk(self.snippet_list)

    # Light Catppuccin Mocha accents, readable on the dark background.
    TYPE_COLORS = {
        "command": "#f38ba8",   # red
        "template": "#89b4fa",  # blue
        "variable": "#a6e3a1",  # green
    }
    DISABLED_COLOR = "#9a9aa5"  # briefing muted for disabled snippets

    def _add_snippet_item(self, parent, trigger, snippet):
        # Snippets have no display name, only a trigger, so the row reads
        # ";sig • used 42x" (or "• never used"), with a star for favourites.
        usage = int(snippet.get("usage", 0))
        usage_text = f"used {usage}x" if usage else "never used"
        star = "\u2605 " if snippet.get("favorite") else ""
        item = QTreeWidgetItem(parent, [f"{star}{trigger} \u2022 {usage_text}"])
        item.setData(0, Qt.UserRole, trigger)
        item.setSizeHint(0, QSize(0, 22 if getattr(self, "_density", "comfortable") == "compact" else 28))
        snippet_type = snippet.get("type", "variable")
        category = snippet.get("category", "uncategorized")
        enabled = snippet.get("enabled", True)
        item.setForeground(
            0, QColor(self.DISABLED_COLOR if not enabled else self.TYPE_COLORS.get(snippet_type, "#d6d6de"))
        )
        state = " | DISABLED" if not enabled else ""
        fav = " | Favourite" if snippet.get("favorite") else ""
        item.setToolTip(0, f"{snippet_type.capitalize()} | Category: {category}{state}{fav}")
        return item

    def on_category_changed(self, category):
        """Handle category dropdown change"""
        if self.daemon:
            self.daemon.set_category(category)
        self.refresh_snippet_list()
        print(f"UI: Showing {category} snippets")

    def _clear_preview(self):
        """Empty the structured preview and disable its buttons."""
        if not hasattr(self, "preview_detail_labels"):
            # Called before the preview area is built (e.g. an early
            # refresh during setup): only the text widget may exist.
            if hasattr(self, "preview_text"):
                self.preview_text.clear()
            return
        for label in self.preview_detail_labels.values():
            label.setText("\u2014")
        self.preview_used_label.setText("Select a snippet to preview")
        if hasattr(self, "preview_note_label"):
            self.preview_note_label.setText("")
            self.preview_note_label.setVisible(False)
        self.preview_text.clear()
        self._preview_raw_text = ""
        self._preview_rendered_text = ""
        if hasattr(self, "preview_missing_label"):
            self.preview_missing_label.setText("")
        self.preview_copy_btn.setEnabled(False)
        self.preview_insert_btn.setEnabled(False)

    def copy_preview(self):
        """Copy the rendered expansion (even in raw preview mode)."""
        text = self._preview_rendered_text or self.preview_text.toPlainText()
        if not text:
            return
        QApplication.clipboard().setText(text)
        self.statusBar().showMessage("Copied expansion to clipboard", 3000)

    def insert_preview(self):
        """Hide the manager and insert the previewed expansion.

        Reuses the picker's insert path (manager-owned timer + daemon
        insert), so the app that takes focus after the manager hides
        receives the text. Focus behaviour is therefore the same caveat
        as the picker: on X11 with xdotool the picker restores a captured
        window, but here there is no captured target, so the window
        manager decides which app takes focus.
        """
        trigger = self.selected_trigger()
        if not trigger or not self.daemon:
            return
        snippet = self.daemon.snippets.get(trigger)
        if not snippet or not snippet.get("enabled", True):
            return
        text = self.get_expansion_preview(snippet)
        if not text:
            return
        self.hide_window()
        self._schedule_picker_insert(trigger, text, 300)

    def on_selection_changed(self):
        self._sync_bulk_bar()
        self._sync_values_section_visibility()
        self._sync_right_content_height()
        trigger = self.selected_trigger()
        if not trigger or not self.daemon:
            self._clear_preview()
            return
        snippet = self.daemon.snippets.get(trigger)
        if not snippet:
            self._clear_preview()
            return

        preview = self.get_expansion_preview(snippet)
        snippet_category = snippet.get("category", "uncategorized")
        snippet_type = snippet.get("type", "variable")
        tags = snippet.get("tags") or []
        used = int(snippet.get("usage", 0))
        last_used = snippet.get("last_used", "never")
        enabled = snippet.get("enabled", True)

        self.preview_detail_labels["category"].setText(snippet_category)
        self.preview_detail_labels["tags"].setText(", ".join(tags) if tags else "none")
        self.preview_detail_labels["type"].setText(snippet_type)
        self.preview_detail_labels["trigger"].setText(trigger)
        disabled_note = " \u2014 DISABLED, will not expand" if not enabled else ""
        self.preview_used_label.setText(
            f"Used {used} times (last: {last_used}){disabled_note}"
        )
        note = snippet.get("note", "")
        if hasattr(self, "preview_note_label"):
            self.preview_note_label.setText(f"Note: {note}" if note else "")
            self.preview_note_label.setVisible(bool(note))
        self._preview_raw_text = self._raw_expansion(snippet)
        self._preview_rendered_text = preview
        self._update_preview_display()
        self.preview_copy_btn.setEnabled(bool(preview))
        self.preview_insert_btn.setEnabled(bool(preview) and enabled)

    def on_language_changed(self, lang):
        self.refresh_snippet_list()
        self.on_selection_changed()
        if self.daemon:
            # Persists to config.json so the language survives restarts.
            self.daemon.set_language(lang)
            print(f"UI: Programming language switched to {lang}")

    def hide_window(self):
        self.hide()

    def bring_to_front(self):
        self.show()
        self.setWindowState(Qt.WindowNoState)
        self.raise_()
        self.activateWindow()
        self.setFocus()
        # On GNOME/X11, activateWindow() alone may not steal focus.
        # requestActivate() on the window handle is more reliable.
        handle = self.windowHandle()
        if handle:
            handle.requestActivate()

    def restart_app(self):
        self.quit_app()

    def trigger_conflicts(self, trigger, exclude=None):
        """Human-readable conflict messages for a trigger, if any.

        Covers exact duplicates (silent overwrite) and the prefix-deferral
        cases (a trigger that is a prefix of another, or vice versa), so the
        user isn't surprised by the daemon's boundary-key behavior.
        """
        if not self.daemon:
            return []
        conflicts = []
        others = [t for t in self.daemon.snippets if t != exclude]
        if trigger in others:
            conflicts.append(
                f"'{trigger}' already exists - adding it will overwrite the existing snippet."
            )
        longer = [t for t in others if t.startswith(trigger)]
        if longer:
            conflicts.append(
                f"'{trigger}' is a prefix of {', '.join(repr(t) for t in longer)} - "
                "it will only expand when a boundary key (space/enter/punctuation) follows."
            )
        shorter = [t for t in others if trigger.startswith(t)]
        if shorter:
            conflicts.append(
                f"'{trigger}' extends existing {', '.join(repr(t) for t in shorter)} - "
                "those will now only expand when a boundary key follows."
            )
        return conflicts

    def _confirm_trigger_conflicts(self, trigger, exclude=None):
        """Warn about trigger conflicts; return True if the user wants to proceed."""
        conflicts = self.trigger_conflicts(trigger, exclude=exclude)
        if not conflicts:
            return True
        message = "Potential trigger conflicts:\n\n" + "\n".join(
            f"\u2022 {c}" for c in conflicts
        ) + "\n\nSave anyway?"
        reply = QMessageBox.question(
            self, "Trigger conflict", message,
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        return reply == QMessageBox.Yes

    def add_snippet(self):
        categories = self.daemon.get_categories() if self.daemon else []
        dialog = AddSnippetDialog(self, categories=categories, daemon=self.daemon)
        if dialog.exec() == QDialog.Accepted:
            snippet = dialog.get_snippet()
            if snippet and self.daemon:
                trigger = snippet.pop("trigger")
                if not self._confirm_trigger_conflicts(trigger):
                    return
                self._push_undo(f"Add snippet '{trigger}'")
                self.daemon.add_or_update_snippet(trigger, snippet)
                self.refresh_snippet_list()
                print(f"UI: Added snippet '{trigger}' (saved)")

    def edit_snippet(self):
        trigger = self.selected_trigger()
        if not trigger or not self.daemon:
            return
        snippet = {"trigger": trigger, **self.daemon.snippets.get(trigger, {})}

        categories = self.daemon.get_categories() if self.daemon else []
        dialog = AddSnippetDialog(self, snippet=snippet, categories=categories, daemon=self.daemon)
        if dialog.exec() == QDialog.Accepted:
            new_snippet = dialog.get_snippet()
            if new_snippet and self.daemon:
                new_trigger = new_snippet.pop("trigger")
                if not self._confirm_trigger_conflicts(new_trigger, exclude=trigger):
                    return
                old_stored = self.daemon.snippets.get(trigger, {})
                new_snippet = self._with_history(old_stored, new_snippet)
                self._push_undo(f"Edit snippet '{trigger}'")
                self.daemon.replace_snippet(trigger, new_trigger, new_snippet)
                self.refresh_snippet_list()
                print(f"UI: Edited snippet '{new_trigger}' (saved)")

    def duplicate_snippet(self):
        """Clone the selected snippet into a pre-filled Add dialog."""
        trigger = self.selected_trigger()
        if not trigger or not self.daemon:
            return
        source = dict(self.daemon.snippets.get(trigger, {}))
        # Pre-fill everything except the trigger so the user types a new one.
        dialog = AddSnippetDialog(self, snippet=source, categories=self.daemon.get_categories(), daemon=self.daemon)
        dialog.setWindowTitle(f"Duplicate '{trigger}'")
        dialog.trigger_input.setFocus()
        if dialog.exec() == QDialog.Accepted:
            new_snippet = dialog.get_snippet()
            if new_snippet and self.daemon:
                new_trigger = new_snippet.pop("trigger")
                if not new_trigger:  # user left the trigger empty
                    return
                if not self._confirm_trigger_conflicts(new_trigger):
                    return
                self._push_undo(f"Duplicate snippet as '{new_trigger}'")
                self.daemon.add_or_update_snippet(new_trigger, new_snippet)
                self.refresh_snippet_list()
                print(f"UI: Duplicated snippet '{trigger}' as '{new_trigger}' (saved)")

    def toggle_snippet_enabled(self):
        """Flip the enabled flag of the selected snippet and persist it."""
        trigger = self.selected_trigger()
        if not trigger or not self.daemon:
            return
        snippet = dict(self.daemon.snippets.get(trigger, {}))
        snippet["enabled"] = not snippet.get("enabled", True)
        self._push_undo(f"{'Disable' if not snippet['enabled'] else 'Enable'} snippet '{trigger}'")
        self.daemon.add_or_update_snippet(trigger, snippet)
        self.refresh_snippet_list()
        state = "Disabled" if not snippet["enabled"] else "Enabled"
        print(f"UI: {state} snippet '{trigger}' (saved)")

    def toggle_snippet_favorite(self):
        """Flip the favourite flag of the selected snippet and persist it.

        Favourites appear in the tree's Favourites section; the flag is
        stored on the snippet dict itself, so it survives restarts and
        is carried by exports (usage/last_used are stripped by packs,
        but favourite is not usage data).
        """
        trigger = self.selected_trigger()
        if not trigger or not self.daemon:
            return
        snippet = dict(self.daemon.snippets.get(trigger, {}))
        snippet["favorite"] = not snippet.get("favorite", False)
        self._push_undo(f"{'Favourite' if snippet['favorite'] else 'Unfavourite'} snippet '{trigger}'")
        self.daemon.add_or_update_snippet(trigger, snippet)
        self.refresh_snippet_list()
        state = "Favourited" if snippet["favorite"] else "Unfavourited"
        print(f"UI: {state} snippet '{trigger}' (saved)")

    def delete_snippet(self):
        if self._is_table_view() and len(self.selected_triggers()) > 1:
            self.bulk_delete()
            return
        trigger = self.selected_trigger()
        if not trigger or not self.daemon:
            return
        reply = QMessageBox.question(self, "Confirm", f"Delete '{trigger}'?")
        if reply == QMessageBox.Yes:
            self._push_undo(f"Delete snippet '{trigger}'", trash_triggers=[trigger])
            self.daemon.delete_snippet(trigger)
            self.refresh_snippet_list()
            self.statusBar().showMessage(
                f"'{trigger}' moved to Trash (restore from Trash)", 4000
            )
            print(f"UI: Deleted snippet '{trigger}' (moved to Trash, saved)")

    def _sync_toggle_text(self):
        """Label the toggle buttons with their state, not just a color.

        Color is a fast glance cue, but text carries the state for
        accessibility (WCAG 1.4.1: don't rely on color alone)."""
        self.toggle_daemon_btn.setText(
            "Daemon: On" if self.toggle_daemon_btn.isChecked() else "Daemon: Off"
        )
        self.toggle_writing_btn.setText(
            "Human Typing: On" if self.toggle_writing_btn.isChecked() else "Human Typing: Off"
        )

    def set_daemon_enabled(self, enabled):
        """Central pause/resume: update daemon state and every UI reflection.

        Always use this instead of setting daemon.enabled directly, so the
        Daemon button, tray icon, tooltip, and status label stay in sync no
        matter which entry point (button, tray menu, hotkey) changed state."""
        if self.daemon:
            self.daemon.enabled = enabled
        self.last_enabled_state = enabled
        status = "enabled" if enabled else "paused"

        self.toggle_daemon_btn.blockSignals(True)
        self.toggle_daemon_btn.setChecked(enabled)
        self.toggle_daemon_btn.blockSignals(False)
        self._sync_toggle_text()

        self.tray_icon.setIcon(create_status_icon("green" if enabled else "red"))
        self.tray_icon.setToolTip(f"Texter - {status.capitalize()}")
        print(f"UI: Daemon {status}")

    def toggle_pause(self):
        """Tray 'Toggle Pause' action: flip the current daemon state.

        QAction.triggered passes checked=False for non-checkable actions,
        so this must not reuse toggle_daemon(checked) — it would always
        pause and never resume."""
        if self.daemon:
            self.set_daemon_enabled(not self.daemon.enabled)

    def toggle_daemon(self, checked):
        if not self.daemon:
            return
        self.set_daemon_enabled(bool(checked))

    def toggle_writing_mode(self, checked):
        if not self.daemon:
            return
        self.daemon.writing_mode = checked
        self.toggle_writing_btn.blockSignals(True)
        self.toggle_writing_btn.setChecked(checked)
        self.toggle_writing_btn.blockSignals(False)
        self._sync_toggle_text()
        print(f"UI: Human typing {'enabled' if checked else 'disabled'}")

    def import_json(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "Import Snippets", "", "JSON Files (*.json)")
        if not file_path:
            return
        try:
            with open(file_path, "r") as f:
                raw = json.load(f)
            # Accept both the dict format ({trigger: snippet}) and the legacy
            # list format ([{"trigger": ..., ...}]); normalize to a dict.
            if isinstance(raw, list):
                snippets = {
                    item.get("trigger", ""): {k: v for k, v in item.items() if k != "trigger"}
                    for item in raw if isinstance(item, dict) and item.get("trigger")
                }
            elif isinstance(raw, dict):
                snippets = raw
            else:
                raise ValueError("file is neither a snippet dict nor a list")

            if not snippets:
                QMessageBox.information(self, "Import", "No snippets found in the file.")
                return

            if self.daemon:
                overwritten = sorted(set(self.daemon.snippets) & set(snippets))
                if overwritten:
                    preview = ", ".join(repr(t) for t in overwritten[:15])
                    if len(overwritten) > 15:
                        preview += f", ... ({len(overwritten)} total)"
                    reply = QMessageBox.question(
                        self, "Import conflict",
                        f"Import will overwrite {len(overwritten)} existing snippet(s):\n\n"
                        f"{preview}\n\nContinue?",
                        QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
                    )
                    if reply != QMessageBox.Yes:
                        return
                self._push_undo(f"Import {len(snippets)} snippet(s)")
                self.daemon.import_snippets(snippets)
                self.refresh_snippet_list()
                self.find_missing_user_settings(notify=False)
                print(f"UI: Imported {len(snippets)} snippets (saved)")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to import: {e}")

    def export_json(self):
        file_path, _ = QFileDialog.getSaveFileName(self, "Export Snippets", "", "JSON Files (*.json)")
        if file_path:
            try:
                if self.daemon:
                    with self.daemon.snippets_lock:
                        snapshot = dict(self.daemon.snippets)
                else:
                    snapshot = {}
                with open(file_path, "w") as f:
                    json.dump(snapshot, f, indent=2)
                print(f"UI: Exported snippets to {file_path}")
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Failed to export: {e}")
