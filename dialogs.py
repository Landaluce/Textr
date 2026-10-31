# texter_mvp/dialogs.py
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
    QLabel, QLineEdit, QTextEdit, QPushButton, QComboBox,
    QListWidget, QListWidgetItem, QDialog, QFormLayout,
    QMessageBox, QFileDialog, QGroupBox, QSystemTrayIcon, QMenu,
    QCheckBox, QTreeWidget, QTreeWidgetItem, QAbstractItemView,
    QStyledItemDelegate, QStyle, QStyleOptionViewItem,
    QTableWidget, QTableWidgetItem, QHeaderView, QDialogButtonBox,
    QRadioButton, QScrollArea, QFrame, QInputDialog
)
from PySide6.QtCore import Qt, QTimer, Signal, QRectF, QRect, QSettings, QEvent
from PySide6.QtGui import (
    QIcon, QAction, QPixmap, QPainter, QColor, QPalette, QPainterPath,
    QShortcut, QKeySequence,
)

from stylesheet import THEME_COLORS

from snippet_helpers import (
    BUILTIN_PLACEHOLDERS,
    _fuzzy_score,
    _normalized_expansion,
    history_entry,
)


class AddSnippetDialog(QDialog):
    def __init__(self, parent=None, snippet=None, categories=None, daemon=None):
        super().__init__(parent)
        self.snippet = snippet
        self.categories = categories or []
        # Daemon (for rendering the live preview and checking settings):
        # explicit argument wins, otherwise the parent window's daemon.
        self.daemon = daemon
        if self.daemon is None and parent is not None:
            self.daemon = getattr(parent, "daemon", None)
        self.original_trigger = snippet.get("trigger") if isinstance(snippet, dict) else None
        self.setup_ui()

    def setup_ui(self):
        self.setWindowTitle("Add/Edit Snippet")
        self.setMinimumWidth(450)

        layout = QFormLayout(self)

        # Trigger
        self.trigger_input = QLineEdit()
        self.trigger_input.setPlaceholderText("e.g., mn, func, date")
        layout.addRow("Trigger:", self.trigger_input)

        # Category
        self.category_input = QComboBox()
        self.category_input.setEditable(True)  # Allow new categories
        self.category_input.addItems(["uncategorized"] + self.categories)
        layout.addRow("Category:", self.category_input)

        # Tags (multiple, comma-separated)
        self.tags_input = QLineEdit()
        self.tags_input.setPlaceholderText("Comma-separated, e.g. python, web")
        layout.addRow("Tags:", self.tags_input)

        # Note (item 4): what this snippet is for; shown in the
        # preview and included in search. Optional, free text.
        self.note_input = QLineEdit()
        self.note_input.setPlaceholderText("What this snippet is for (optional)")
        layout.addRow("Note:", self.note_input)

        # Type
        self.type_combo = QComboBox()
        self.type_combo.addItems(["variable", "template", "command"])
        self.type_combo.currentTextChanged.connect(self.on_type_changed)
        layout.addRow("Type:", self.type_combo)

        # Placeholder inserter (item 3): pick a placeholder and insert
        # it at the cursor, instead of typing {name} by hand. Hidden
        # for command snippets, which have no expansion text.
        self.placeholder_row = QWidget()
        _ph_layout = QHBoxLayout(self.placeholder_row)
        _ph_layout.setContentsMargins(0, 0, 0, 0)
        self.placeholder_combo = QComboBox()
        for _name in self._placeholder_names():
            self.placeholder_combo.addItem("{" + _name + "}", _name)
        _ph_layout.addWidget(self.placeholder_combo)
        self.placeholder_insert_btn = QPushButton("Insert")
        self.placeholder_insert_btn.setToolTip(
            "Insert the selected placeholder at the cursor in the value field"
        )
        self.placeholder_insert_btn.clicked.connect(self._insert_placeholder)
        _ph_layout.addWidget(self.placeholder_insert_btn)
        layout.addRow("Insert placeholder:", self.placeholder_row)

        # Value
        self.value_input = QTextEdit()
        self.value_input.setPlaceholderText("For variable: the value")
        self.value_input.setFixedHeight(60)
        layout.addRow("Value:", self.value_input)

        # Languages
        self.languages_input = QTextEdit()
        self.languages_input.setPlaceholderText('For template: JSON of programming languages\n{"python": "def f(): pass", "javascript": "function f() {}"}')
        self.languages_input.setFixedHeight(80)
        layout.addRow("Programming languages (JSON):", self.languages_input)

        # Action (and optional value, e.g. the language for set_language)
        self.action_input = QLineEdit()
        self.action_input.setPlaceholderText("For command: set_language, toggle_pause, shutdown")
        layout.addRow("Action:", self.action_input)
        self.command_value_input = QLineEdit()
        self.command_value_input.setPlaceholderText("For set_language: the language name, e.g. java")
        layout.addRow("Value:", self.command_value_input)

        # Enabled (default on; disabled snippets never expand)
        self.enabled_check = QCheckBox("Enabled")
        self.enabled_check.setChecked(self.snippet.get("enabled", True) if self.snippet else True)
        layout.addRow(self.enabled_check)

        # Populate fields if editing
        if self.snippet:
            self.trigger_input.setText(self.snippet.get("trigger", ""))
            self.note_input.setText(self.snippet.get("note", ""))
            self.category_input.setCurrentText(self.snippet.get("category", "uncategorized"))
            self.tags_input.setText(", ".join(self.snippet.get("tags") or []))
            self.type_combo.setCurrentText(self.snippet.get("type", "variable"))
            self.on_type_changed(self.snippet.get("type", "variable"))

            if self.snippet.get("type") == "variable":
                self.value_input.setText(self.snippet.get("value", ""))
            elif self.snippet.get("type") == "template":
                langs = self.snippet.get("languages", {})
                self.languages_input.setText(json.dumps(langs, indent=2))
            elif self.snippet.get("type") == "command":
                self.action_input.setText(self.snippet.get("action", ""))
                self.command_value_input.setText(self.snippet.get("value", ""))

        # Live preview + warnings: rendered expansion and trigger /
        # missing-setting warnings, updated as the user types (see
        # _update_live_preview). Placed before the buttons so it is
        # visible while editing, not hidden below them.
        self.live_preview = QTextEdit()
        self.live_preview.setReadOnly(True)
        self.live_preview.setFixedHeight(60)
        layout.addRow("Live preview:", self.live_preview)
        self.warning_label = QLabel("")
        self.warning_label.setWordWrap(True)
        layout.addRow(self.warning_label)

        # Try-it sandbox (item 2): hidden until toggled, so the dialog
        # stays compact. Fill in placeholder values, see the rendered
        # result, without saving or expanding in another app.
        self._tryit_signature = None
        self._tryit_inputs = {}
        self._tryit_open = False
        self.tryit_btn = QPushButton("Try it \u25b8")
        self.tryit_btn.setCheckable(True)
        self.tryit_btn.setChecked(False)
        self.tryit_btn.setToolTip("Test this snippet with sample placeholder values")
        self.tryit_btn.toggled.connect(self._on_tryit_toggled)
        layout.addRow(self.tryit_btn)
        self.tryit_widget = QWidget()
        _tryit_layout = QVBoxLayout(self.tryit_widget)
        _tryit_layout.setContentsMargins(0, 0, 0, 0)
        self.tryit_form = QFormLayout()
        _tryit_layout.addLayout(self.tryit_form)
        _tryit_layout.addWidget(QLabel("Result:"))
        self.tryit_output = QTextEdit()
        self.tryit_output.setReadOnly(True)
        self.tryit_output.setFixedHeight(60)
        _tryit_layout.addWidget(self.tryit_output)
        self.tryit_widget.setHidden(True)
        layout.addRow(self.tryit_widget)

        # Buttons
        btn_layout = QHBoxLayout()
        self.save_btn = QPushButton("Save")
        self.save_btn.clicked.connect(self.accept)
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(self.save_btn)
        layout.addRow(btn_layout)

        # Apply the initial type-based visibility (new dialogs default to
        # variable; edit flows call on_type_changed again with their type).
        self.on_type_changed(self.type_combo.currentText())

        # Live preview: refresh on every keystroke in the fields that
        # feed the expansion, and on type switches.
        self.trigger_input.textChanged.connect(self._update_live_preview)
        self.value_input.textChanged.connect(self._update_live_preview)
        self.languages_input.textChanged.connect(self._update_live_preview)
        self.action_input.textChanged.connect(self._update_live_preview)
        self.command_value_input.textChanged.connect(self._update_live_preview)
        self.type_combo.currentTextChanged.connect(self._update_live_preview)
        self.value_input.textChanged.connect(self._refresh_tryit)
        self.languages_input.textChanged.connect(self._refresh_tryit)
        self.type_combo.currentTextChanged.connect(self._refresh_tryit)
        self._update_live_preview()
        # "{" popup (item 3): typing "{" in a value field offers the
        # placeholder list at the cursor. The popup is scheduled after
        # the keystroke so the "{" itself is already in the text.
        self.value_input.installEventFilter(self)
        self.languages_input.installEventFilter(self)

    def on_type_changed(self, type_val):
        self.languages_input.setHidden(type_val != "template")
        self.action_input.setHidden(type_val != "command")
        self.command_value_input.setHidden(type_val != "command")
        if hasattr(self, "placeholder_row"):
            self.placeholder_row.setHidden(type_val == "command")

    def eventFilter(self, watched, event):
        """Offer the placeholder popup when "{" is typed in a value field."""
        if (
            watched in (getattr(self, "value_input", None), getattr(self, "languages_input", None))
            and event.type() == QEvent.KeyPress
            and event.text() == "{"
        ):
            QTimer.singleShot(0, lambda: self._show_placeholder_popup(watched))
        return super().eventFilter(watched, event)

    def _placeholder_names(self):
        """Placeholder names for the inserter: built-ins, custom settings, offsets."""
        names = ["name", "email", "date", "time", "iso", "week", "clipboard", "cursor"]
        if self.daemon is not None:
            config = getattr(self.daemon, "config", None)
            if isinstance(config, dict):
                names.extend(sorted((config.get("user_settings", {}) or {}).keys()))
        names.extend(["date+7", "date-7", "iso+1", "time+2", "week+1"])
        names.append("choice:Formal|Friendly")
        seen = set()
        out = []
        for name in names:
            if name and name not in seen:
                seen.add(name)
                out.append(name)
        return out

    def _placeholder_target(self):
        """Text field the inserter writes into for the current type."""
        if self.type_combo.currentText() == "variable":
            return self.value_input
        if self.type_combo.currentText() == "template":
            return self.languages_input
        return None

    def _insert_placeholder(self):
        """Insert the combo's placeholder at the cursor (Insert button)."""
        editor = self._placeholder_target()
        if editor is None:
            return
        name = self.placeholder_combo.currentData()
        if not name:
            return
        editor.setFocus()
        editor.textCursor().insertText("{" + str(name) + "}")

    def _insert_placeholder_name(self, editor, name):
        """Insert {name} at the cursor, eating a just-typed "{" (popup path)."""
        cursor = editor.textCursor()
        pos = cursor.position()
        if pos > 0 and editor.document().characterAt(pos - 1) == "{":
            cursor.deletePreviousChar()
        cursor.insertText("{" + str(name) + "}")
        editor.setTextCursor(cursor)

    def _show_placeholder_popup(self, editor):
        """Menu of placeholders at the text cursor, after typing "{"."""
        if editor is None or not editor.hasFocus():
            return
        menu = QMenu(editor)
        for name in self._placeholder_names():
            action = menu.addAction("{" + name + "}")
            action.triggered.connect(
                lambda _c, n=name: self._insert_placeholder_name(editor, n)
            )
        rect = editor.cursorRect()
        menu.exec(editor.mapToGlobal(rect.bottomRight()))

    def _on_tryit_toggled(self, checked):
        self._tryit_open = bool(checked)
        self.tryit_widget.setHidden(not checked)
        self.tryit_btn.setText("Try it \u25be" if checked else "Try it \u25b8")
        if checked:
            self._refresh_tryit()

    def _tryit_placeholders(self, raw):
        """(setting names, choices) found in `raw`, in first-use order."""
        names = []
        for name in re.findall(
            r"\{([A-Za-z_][A-Za-z0-9_]*)(?:[+-]\d+)?\}", raw or ""
        ):
            builtins = {"name", "email", "date", "time", "iso", "week", "clipboard", "cursor"}
            if name not in builtins and name not in names:
                names.append(name)
        choices = []
        if self.daemon is not None:
            fn = getattr(self.daemon, "choice_placeholders", None)
            if callable(fn):
                try:
                    choices = fn(raw)
                except Exception:
                    choices = []
        return names, choices

    def _refresh_tryit(self, *args):
        """Rebuild try-it inputs only when placeholders change; else refresh output."""
        if not hasattr(self, "tryit_widget") or not getattr(self, "_tryit_open", False):
            return
        raw, json_error = self._raw_expansion_text()
        names, choices = self._tryit_placeholders(raw)
        signature = (raw, tuple(names), tuple(label for label, _o in choices))
        if signature != self._tryit_signature:
            self._tryit_signature = signature
            while self.tryit_form.count():
                _item = self.tryit_form.takeAt(0)
                _widget = _item.widget()
                if _widget is not None:
                    _widget.deleteLater()
            self._tryit_inputs = {}
            for name in names:
                edit = QLineEdit()
                edit.setPlaceholderText(f"Value for {{{name}}}")
                edit.textChanged.connect(self._update_tryit_output)
                self.tryit_form.addRow(f"{name}:", edit)
                self._tryit_inputs[name] = edit
            for label, options in choices:
                combo = QComboBox()
                combo.addItems(list(options))
                combo.currentTextChanged.connect(self._update_tryit_output)
                self.tryit_form.addRow("Choice:", combo)
                self._tryit_inputs[label] = combo
        self._update_tryit_output()

    def _update_tryit_output(self, *args):
        if not hasattr(self, "tryit_output"):
            return
        raw, json_error = self._raw_expansion_text()
        if json_error:
            self.tryit_output.setPlainText("(Invalid JSON for programming languages)")
            return
        values = {}
        for key, widget in self._tryit_inputs.items():
            if isinstance(widget, QComboBox):
                values[key] = widget.currentText()
            elif widget.text().strip():
                values[key] = widget.text()
        if self.daemon is not None and hasattr(self.daemon, "render_template"):
            try:
                self.tryit_output.setPlainText(
                    self.daemon.render_template(raw, prompt_values=values)
                )
                return
            except Exception:
                pass
        self.tryit_output.setPlainText(raw)

    def _raw_expansion_text(self):
        """(raw text, json_error) for the type currently being edited."""
        snippet_type = self.type_combo.currentText()
        if snippet_type == "variable":
            return self.value_input.toPlainText(), False
        if snippet_type == "template":
            raw_json = self.languages_input.toPlainText().strip()
            if not raw_json:
                return "", False
            try:
                langs = json.loads(raw_json)
            except json.JSONDecodeError:
                return "", True
            if not isinstance(langs, dict):
                return "", True
            current = getattr(self.daemon, "current_language", None) if self.daemon else None
            if current in langs:
                lang = current
            elif "python" in langs:
                lang = "python"
            elif langs:
                lang = next(iter(langs))
            else:
                lang = None
            return (str(langs.get(lang, "")) if lang else ""), False
        action = self.action_input.text().strip()
        value = self.command_value_input.text().strip()
        return f"Command: {action}" + (f" ({value})" if value else ""), False

    def _missing_placeholders(self, raw):
        """Placeholder names in `raw` with no built-in and no saved value."""
        if self.daemon is None:
            return []
        names = re.findall(r"\{([A-Za-z_][A-Za-z0-9_]*)(?:[+-]\d+)?\}", raw or "")
        builtins = set(BUILTIN_PLACEHOLDERS) | {"cursor"}
        settings = {}
        config = getattr(self.daemon, "config", None)
        if isinstance(config, dict):
            settings = config.get("user_settings", {}) or {}
        return sorted(
            {n for n in names if n not in builtins and not str(settings.get(n, "")).strip()}
        )

    def _update_live_preview(self, *args):
        """Refresh the live preview, warnings, and Save availability."""
        if not hasattr(self, "live_preview"):
            return
        raw, json_error = self._raw_expansion_text()
        snippet_type = self.type_combo.currentText()
        if json_error:
            preview = "(Invalid JSON for programming languages)"
        elif snippet_type == "command":
            preview = raw
        elif self.daemon is not None and hasattr(self.daemon, "render_template"):
            try:
                preview = self.daemon.render_template(raw)
            except Exception:
                preview = raw
        else:
            preview = raw
        self.live_preview.setPlainText(preview)

        warnings = []
        trigger = self.trigger_input.text().strip()
        if not trigger:
            warnings.append("Trigger is required.")
        else:
            conflicts = None
            window = self.parent()
            if window is not None and hasattr(window, "trigger_conflicts"):
                try:
                    conflicts = window.trigger_conflicts(trigger, exclude=self.original_trigger)
                except Exception:
                    conflicts = None
            if conflicts is None and self.daemon is not None:
                others = [t for t in getattr(self.daemon, "snippets", {}) if t != self.original_trigger]
                conflicts = []
                if trigger in others:
                    conflicts.append(f"'{trigger}' already exists - saving will overwrite it.")
            warnings.extend(conflicts or [])
        if json_error:
            warnings.append("Fix the JSON above before saving.")
        elif snippet_type != "command":
            missing = self._missing_placeholders(raw)
            if missing:
                warnings.append(
                    "Missing settings: " + ", ".join(f"{{{n}}}" for n in missing)
                    + " - no value set; expansion will prompt for them."
                )
        self.warning_label.setText("\n".join(warnings))
        self.warning_label.setStyleSheet("color: #f38ba8;" if warnings else "")
        if hasattr(self, "save_btn"):
            self.save_btn.setEnabled(bool(trigger) and not json_error)

    def get_snippet(self):
        trigger = self.trigger_input.text().strip()
        snippet_type = self.type_combo.currentText()
        category = self.category_input.currentText().strip() or "uncategorized"
        raw_tags = [t.strip() for t in self.tags_input.text().split(",") if t.strip()]

        snippet = {"trigger": trigger, "type": snippet_type, "category": category}
        note = self.note_input.text().strip()
        if note:
            snippet["note"] = note
        if raw_tags:
            snippet["tags"] = raw_tags
        if not self.enabled_check.isChecked():
            snippet["enabled"] = False

        if snippet_type == "variable":
            snippet["value"] = self.value_input.toPlainText().strip()
        elif snippet_type == "template":
            try:
                langs = json.loads(self.languages_input.toPlainText())
                snippet["languages"] = langs
            except json.JSONDecodeError:
                QMessageBox.critical(self, "Error", "Invalid JSON for programming languages")
                return None
        elif snippet_type == "command":
            snippet["action"] = self.action_input.text().strip()
            cmd_value = self.command_value_input.text().strip()
            if cmd_value:
                snippet["value"] = cmd_value

        return snippet

class PromptValuesDialog(QDialog):
    """Ask for per-use placeholder values at expansion time.

    Standalone (no parent, stays on top) so that when it closes, focus
    returns to the application the user was typing in, not to the
    manager window. Returns None on cancel.
    """

    def __init__(self, keys, choices=None):
        super().__init__(None)
        self.setWindowTitle("Fill in snippet values")
        self.setWindowFlags(
            self.windowFlags() | Qt.WindowStaysOnTopHint | Qt.Tool
        )
        self._edits = {}
        self._choice_edits = {}
        layout = QVBoxLayout(self)
        form = QFormLayout()
        for key in keys:
            edit = QLineEdit()
            edit.setPlaceholderText(f"Value for {{{key}}}")
            self._edits[key] = edit
            form.addRow(f"{key}:", edit)
        # Choice placeholders (item 1): one dropdown per distinct
        # {choice:A|B}; the value key is the full placeholder text.
        for label, options in choices or []:
            combo = QComboBox()
            combo.addItems(list(options))
            self._choice_edits[label] = combo
            form.addRow("Choice:", combo)
        layout.addLayout(form)
        self._save_check = QCheckBox("Save these values as custom settings")
        self._save_check.setChecked(True)
        layout.addWidget(self._save_check)
        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        if self._edits:
            list(self._edits.values())[0].setFocus()

    def values(self):
        out = {k: e.text() for k, e in self._edits.items()}
        out.update(
            {label: combo.currentText() for label, combo in self._choice_edits.items()}
        )
        return out

    def save_checked(self):
        return self._save_check.isChecked()

class QuickInsertDialog(QDialog):
    """Fuzzy snippet picker, opened anywhere via Ctrl+Alt+Space.

    Standalone and stays-on-top so closing it returns focus to the
    application the user was in. Only variable/template snippets are
    listed (commands act on the manager, they do not insert text).
    """

    def __init__(self, daemon, ui, target_window=None, from_hotkey=False):
        super().__init__(None)
        self.daemon = daemon
        self.ui = ui
        self.target_window = target_window
        self.pending_insert = None
        self.setWindowTitle("Quick Insert")
        self.setWindowFlags(
            self.windowFlags() | Qt.WindowStaysOnTopHint | Qt.Tool
        )
        self.resize(520, 380)
        layout = QVBoxLayout(self)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Type to search snippets...")
        self.search.textChanged.connect(self._refresh_list)
        self.search.returnPressed.connect(self._on_accept)
        layout.addWidget(self.search)
        self.list_widget = QListWidget()
        self.list_widget.itemDoubleClicked.connect(lambda _i: self._on_accept())
        self.list_widget.itemActivated.connect(lambda _i: self._on_accept())
        layout.addWidget(self.list_widget)
        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        if (
            from_hotkey
            and target_window is None
            and daemon is not None
            and not daemon.wayland
            and not daemon._xdotool_available()
        ):
            warn = QLabel(
                "xdotool not found: focus restore after picking may be "
                "unreliable. Install it with: sudo apt install xdotool"
            )
            warn.setWordWrap(True)
            layout.addWidget(warn)
        self._refresh_list()
        self.search.setFocus()

    def _candidates(self):
        if not self.daemon:
            return []
        with self.daemon.snippets_lock:
            items = list(self.daemon.snippets.items())
        out = []
        for trigger, snippet in items:
            if not isinstance(snippet, dict):
                continue
            if snippet.get("type") not in ("variable", "template"):
                continue
            if not self.daemon._enabled(snippet):
                continue
            if snippet.get("type") == "variable":
                raw = snippet.get("value", "")
            else:
                raw = (snippet.get("languages", {}) or {}).get(
                    self.daemon.current_language, ""
                ) or ""
            out.append((trigger, snippet, raw))
        return out

    def _add_picker_item(self, trigger, snippet, raw):
        """One picker row: star, trigger, type, preview, usage."""
        preview = " ".join(str(raw).split())[:80]
        usage = int(snippet.get("usage", 0) or 0)
        usage_text = f"used {usage}x" if usage else "never used"
        star = "\u2605 " if snippet.get("favorite") else ""
        item = QListWidgetItem(
            f"{star}{trigger}  [{snippet.get('type', '')}]  {preview}  \u2022 {usage_text}"
        )
        item.setData(Qt.UserRole, trigger)
        self.list_widget.addItem(item)
        return item

    def _add_picker_header(self, title):
        """Non-selectable section header (Recent / Favourites / All)."""
        item = QListWidgetItem(title)
        item.setData(Qt.UserRole, None)
        item.setFlags(Qt.ItemIsEnabled)
        header_font = item.font()
        header_font.setBold(True)
        item.setFont(header_font)
        # Orange headers are readable on dark; on light they need the
        # darker orange, read from the same saved theme as the manager.
        _theme = QSettings("Landaluce", "Textr").value("theme", "dark")
        _colors = THEME_COLORS.get(_theme, THEME_COLORS["dark"])
        item.setForeground(QColor(_colors["picker_header"]))
        self.list_widget.addItem(item)
        return item

    def _refresh_list(self):
        query = self.search.text().strip().lower()
        scored = []
        for trigger, snippet, raw in self._candidates():
            # Best of trigger, tags, and category: a snippet tagged
            # "job" is found by typing "job", not only by its trigger.
            score = _fuzzy_score(trigger, raw, query)
            for _tag in (snippet.get("tags") or []):
                score = max(score, _fuzzy_score(str(_tag), "", query))
            score = max(
                score, _fuzzy_score(str(snippet.get("category", "")), "", query)
            )
            if query and score == 0:
                continue
            usage = int(snippet.get("usage", 0) or 0)
            scored.append((-score, -usage, trigger, snippet, raw))
        scored.sort(key=lambda r: (r[0], r[1], r[2]))
        self.list_widget.clear()
        if not query:
            # Smarter picker (item 6): with no query, Recent (3 most
            # recently used) and Favourites come first as sections,
            # mirroring the tree view, then the rest by usage.
            by_trigger = {t: (sn, raw) for _s, _u, t, sn, raw in scored}
            recent = [
                (t, sn) for t, (sn, _raw) in by_trigger.items()
                if (sn.get("last_used") or "never") != "never"
            ]
            recent.sort(
                key=lambda kv: (kv[1].get("last_used") or "", kv[0].lower()),
                reverse=True,
            )
            recent = recent[:3]
            recent_triggers = {t for t, _sn in recent}
            favorites = [
                (t, sn) for t, (sn, _raw) in by_trigger.items()
                if sn.get("favorite") and t not in recent_triggers
            ]
            favorites.sort(key=lambda kv: (-int(kv[1].get("usage", 0) or 0), kv[0].lower()))
            favorite_triggers = {t for t, _sn in favorites}
            rest = [
                (t, sn) for t, (sn, _raw) in by_trigger.items()
                if t not in recent_triggers and t not in favorite_triggers
            ]
            rest.sort(key=lambda kv: (-int(kv[1].get("usage", 0) or 0), kv[0].lower()))
            for title, group in (
                ("Recent", recent),
                ("Favourites", favorites),
                ("All snippets", rest),
            ):
                if not group:
                    continue
                self._add_picker_header(title)
                for trigger, snippet in group:
                    self._add_picker_item(trigger, snippet, by_trigger[trigger][1])
        else:
            for _neg_score, _neg_usage, trigger, snippet, raw in scored:
                self._add_picker_item(trigger, snippet, raw)
        # Select the first real snippet row, never a section header.
        for row in range(self.list_widget.count()):
            item = self.list_widget.item(row)
            if item is not None and item.data(Qt.UserRole) is not None:
                self.list_widget.setCurrentRow(row)
                item.setSelected(True)
                break

    def _on_accept(self):
        item = self.list_widget.currentItem()
        if item is not None and item.data(Qt.UserRole) is None:
            # A section header was current (e.g. clicked): move to the
            # first snippet row at or after it, so headers never insert.
            item = None
            start = self.list_widget.currentRow()
            for row in range(start, self.list_widget.count()):
                candidate = self.list_widget.item(row)
                if candidate is not None and candidate.data(Qt.UserRole) is not None:
                    item = candidate
                    break
        if item is None:
            selected = self.list_widget.selectedItems()
            item = selected[0] if selected else None
        if item is None and self.list_widget.count():
            # OK pressed with no selection (e.g. selection cleared by
            # clicking empty list space): insert the top result instead
            # of silently doing nothing. Double-click never hits this
            # because it always carries its own item. Skip headers.
            for row in range(self.list_widget.count()):
                candidate = self.list_widget.item(row)
                if candidate is not None and candidate.data(Qt.UserRole) is not None:
                    item = candidate
                    break
        if item is None:
            return
        trigger = item.data(Qt.UserRole)
        if not self.daemon:
            self.reject()
            return
        with self.daemon.snippets_lock:
            snippet = self.daemon.snippets.get(trigger)
        if not isinstance(snippet, dict):
            self.reject()
            return
        if snippet.get("type") == "variable":
            raw = snippet.get("value", "")
        else:
            raw = (snippet.get("languages", {}) or {}).get(
                self.daemon.current_language, ""
            ) or ""
        # Prompt path (GUI thread): do NOT use render_with_prompt here,
        # which would wait on the GUI thread for its own queued dialog.
        missing = self.daemon.missing_placeholders(raw)
        choices = []
        _choice_fn = getattr(self.daemon, "choice_placeholders", None)
        if callable(_choice_fn):
            choices = _choice_fn(raw)
        values = {}
        if missing or choices:
            values = self.ui.prompt_for_values_sync(missing, choices)
            if values is None:
                self.reject()
                return
        final = self.daemon.render_template(raw, prompt_values=values)
        # Do NOT restore focus or schedule the insert here: restoring
        # before accept() is undone when the dialog closes, because Qt
        # (and the window manager, following the mouse) reactivates
        # Textr's manager window on close, and the paste then lands in
        # whatever window ends up on top behind the picker. Instead,
        # stash the pending insert; show_picker() restores the target
        # window AFTER exec() returns and only then schedules the insert.
        self.pending_insert = (trigger, final)
        self.hide()
        self.accept()


class BackupsDialog(QDialog):
    """List config/snippets backups and restore one."""

    def __init__(self, ui):
        super().__init__(ui)
        self.ui = ui
        self.setWindowTitle("Backups")
        self.resize(520, 360)
        layout = QVBoxLayout(self)
        self.list_widget = QListWidget()
        layout.addWidget(self.list_widget)
        self._refresh()
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        restore_btn = QPushButton("Restore selected")
        restore_btn.clicked.connect(self._restore)
        restore_one_btn = QPushButton("Restore one snippet\u2026")
        restore_one_btn.setToolTip(
            "Restore a single snippet from the selected backup, "
            "without replacing the whole library"
        )
        restore_one_btn.clicked.connect(self._restore_one)
        btn_row = QHBoxLayout()
        btn_row.addWidget(restore_btn)
        btn_row.addWidget(restore_one_btn)
        btn_row.addWidget(buttons)
        layout.addLayout(btn_row)

    def _refresh(self):
        self.list_widget.clear()
        if not self.ui.daemon:
            return
        for b in self.ui.daemon.list_backups():
            item = QListWidgetItem(f"{b['kind']}: {b['name']}")
            item.setData(Qt.UserRole, b["path"])
            self.list_widget.addItem(item)

    def _restore(self):
        item = self.list_widget.currentItem()
        if item is None or not self.ui.daemon:
            return
        path = item.data(Qt.UserRole)
        reply = QMessageBox.question(
            self, "Restore backup",
            "Restore this backup? The current file is backed up first, "
            "so you can undo this restore from Backups.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        if self.ui.daemon.restore_backup(path):
            self.ui.refresh_snippet_list()
            self.ui.statusBar().showMessage("Backup restored", 3000)
            self._refresh()
        else:
            QMessageBox.warning(self, "Restore failed", "Could not restore that backup.")

    def _restore_one(self):
        """Restore a single snippet from the selected backup."""
        item = self.list_widget.currentItem()
        if item is None or not self.ui.daemon:
            return
        path = item.data(Qt.UserRole)
        load = getattr(self.ui.daemon, "load_backup_snippets", None)
        snippets = load(path) if callable(load) else None
        if snippets is None:
            QMessageBox.information(
                self, "Restore one snippet",
                "Only snippets backups can restore a single snippet. "
                "Use Restore selected for a config backup.",
            )
            return
        if not snippets:
            QMessageBox.information(
                self, "Restore one snippet", "This backup contains no snippets."
            )
            return
        dialog = BackupSnippetRestoreDialog(
            self.ui, os.path.basename(path), snippets
        )
        if dialog.exec() != QDialog.Accepted:
            return
        trigger = dialog.selected_trigger()
        if not trigger or trigger not in snippets:
            return
        if trigger in self.ui.daemon.snippets:
            reply = QMessageBox.question(
                self, "Restore one snippet",
                f"'{trigger}' already exists. Replace it with the backup version?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                return
        self.ui._push_undo(f"Restore '{trigger}' from backup")
        with self.ui.daemon.snippets_lock:
            self.ui.daemon.snippets[trigger] = deepcopy(snippets[trigger])
        self.ui.daemon.save_snippets()
        self.ui.refresh_snippet_list()
        self.ui.statusBar().showMessage(
            f"Restored '{trigger}' from backup", 3000
        )


class BackupSnippetRestoreDialog(QDialog):
    """Pick one snippet from a backup file to restore into the library."""

    def __init__(self, ui, backup_name, snippets):
        super().__init__(ui)
        self.setWindowTitle(f"Restore one snippet from {backup_name}")
        self.resize(440, 360)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"{len(snippets)} snippet(s) in this backup:"))
        self.list_widget = QListWidget()
        for trigger in sorted(snippets):
            self.list_widget.addItem(trigger)
        layout.addWidget(self.list_widget)
        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def selected_trigger(self):
        item = self.list_widget.currentItem()
        return item.text() if item is not None else None


class TrashDialog(QDialog):
    """Deleted snippets: restore, delete forever, or empty the trash."""

    def __init__(self, ui):
        super().__init__(ui)
        self.ui = ui
        self.setWindowTitle("Trash")
        self.resize(520, 360)
        layout = QVBoxLayout(self)
        self.list_widget = QListWidget()
        layout.addWidget(self.list_widget)
        self._refresh()
        restore_btn = QPushButton("Restore selected")
        restore_btn.clicked.connect(self._restore)
        delete_btn = QPushButton("Delete forever")
        delete_btn.clicked.connect(self._delete_forever)
        empty_btn = QPushButton("Empty trash")
        empty_btn.clicked.connect(self._empty)
        close_btn = QDialogButtonBox(QDialogButtonBox.Close)
        close_btn.rejected.connect(self.reject)
        btn_row = QHBoxLayout()
        for b in (restore_btn, delete_btn, empty_btn, close_btn):
            btn_row.addWidget(b)
        layout.addLayout(btn_row)

    def _refresh(self):
        self.list_widget.clear()
        if not self.ui.daemon:
            return
        for trigger, entry in sorted(self.ui.daemon.load_trash().items()):
            deleted = entry.get("deleted_at", "") if isinstance(entry, dict) else ""
            item = QListWidgetItem(f"{trigger}  (deleted {deleted})")
            item.setData(Qt.UserRole, trigger)
            self.list_widget.addItem(item)

    def _restore(self):
        item = self.list_widget.currentItem()
        if item is None or not self.ui.daemon:
            return
        if self.ui.daemon.restore_snippet(item.data(Qt.UserRole)):
            self.ui.refresh_snippet_list()
            self._refresh()

    def _delete_forever(self):
        item = self.list_widget.currentItem()
        if item is None or not self.ui.daemon:
            return
        reply = QMessageBox.question(
            self, "Delete forever",
            f"Permanently delete '{item.data(Qt.UserRole)}'? This cannot be undone.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if reply == QMessageBox.Yes:
            self.ui.daemon.delete_trash_forever(item.data(Qt.UserRole))
            self._refresh()

    def _empty(self):
        if not self.ui.daemon:
            return
        reply = QMessageBox.question(
            self, "Empty trash",
            "Permanently delete everything in the trash? This cannot be undone.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if reply == QMessageBox.Yes:
            self.ui.daemon.clear_trash()
            self._refresh()


class SnippetHistoryDialog(QDialog):
    """Earlier versions of one snippet, kept when it is edited."""

    def __init__(self, ui, trigger):
        super().__init__(ui)
        self.ui = ui
        self.trigger = trigger
        self.setWindowTitle(f"History: {trigger}")
        self.resize(520, 360)
        layout = QVBoxLayout(self)
        self.list_widget = QListWidget()
        layout.addWidget(self.list_widget)
        snippet = (ui.daemon.snippets.get(trigger, {}) if ui.daemon else {})
        self.entries = list(snippet.get("history") or [])
        for entry in self.entries:
            old = entry.get("snippet", {}) if isinstance(entry, dict) else {}
            if old.get("type") == "template":
                langs = old.get("languages", {}) or {}
                text = next(iter(langs.values()), "") if langs else ""
            elif old.get("type") == "command":
                text = f"Command: {old.get('action', '')}"
            else:
                text = old.get("value", "")
            preview = " ".join(str(text).split())[:80]
            when = entry.get("saved_at", "") if isinstance(entry, dict) else ""
            self.list_widget.addItem(f"{when}  {preview}")
        restore_btn = QPushButton("Restore selected version")
        restore_btn.clicked.connect(self._restore)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        btn_row = QHBoxLayout()
        btn_row.addWidget(restore_btn)
        btn_row.addWidget(buttons)
        layout.addLayout(btn_row)

    def _restore(self):
        row = self.list_widget.currentRow()
        if row < 0 or row >= len(self.entries) or not self.ui.daemon:
            return
        entry = self.entries[row]
        old_snippet = entry.get("snippet", {})
        reply = QMessageBox.question(
            self, "Restore version",
            f"Replace the current '{self.trigger}' with this earlier version? "
            "The current version is kept in history.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        with self.ui.daemon.snippets_lock:
            current = self.ui.daemon.snippets.get(self.trigger, {})
            restored = deepcopy(old_snippet)
            history = [history_entry(current)]
            history.extend(
                e for e in (current.get("history") or [])[:4]
            )
            restored["history"] = history
            self.ui._push_undo(f"Restore earlier version of '{self.trigger}'")
            self.ui.daemon.snippets[self.trigger] = restored
        self.ui.daemon.save_snippets()
        self.ui.statusBar().showMessage(
            f"Restored an earlier version of '{self.trigger}'", 3000
        )
        self.accept()


class PackExportDialog(QDialog):
    """Choose which snippets go into an exported pack file."""

    def __init__(self, ui):
        super().__init__(ui)
        self.ui = ui
        self.setWindowTitle("Export Pack")
        self.resize(520, 400)
        layout = QVBoxLayout(self)
        self.list_widget = QListWidget()
        if ui.daemon:
            with ui.daemon.snippets_lock:
                triggers = sorted(ui.daemon.snippets)
            for trigger in triggers:
                item = QListWidgetItem(trigger)
                item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                item.setCheckState(Qt.Checked)
                self.list_widget.addItem(item)
        layout.addWidget(self.list_widget)
        all_btn = QPushButton("Select all")
        all_btn.clicked.connect(lambda: self._set_all(Qt.Checked))
        none_btn = QPushButton("Select none")
        none_btn.clicked.connect(lambda: self._set_all(Qt.Unchecked))
        row = QHBoxLayout()
        row.addWidget(all_btn)
        row.addWidget(none_btn)
        layout.addLayout(row)
        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _set_all(self, state):
        for i in range(self.list_widget.count()):
            self.list_widget.item(i).setCheckState(state)

    def selected_triggers(self):
        return [
            self.list_widget.item(i).text()
            for i in range(self.list_widget.count())
            if self.list_widget.item(i).checkState() == Qt.Checked
        ]


class PackConflictDialog(QDialog):
    """How to handle pack triggers that already exist with different content."""

    def __init__(self, conflicts):
        super().__init__()
        self.setWindowTitle("Pack import conflicts")
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            f"{len(conflicts)} pack snippet(s) already exist with different "
            "content:\n" + ", ".join(conflicts[:15])
            + (" ..." if len(conflicts) > 15 else "")
        ))
        self.skip_radio = QRadioButton("Skip conflicts (keep existing)")
        self.skip_radio.setChecked(True)
        self.rename_radio = QRadioButton("Rename imported ones (append -2, -3...)")
        self.overwrite_radio = QRadioButton("Overwrite existing snippets")
        for r in (self.skip_radio, self.rename_radio, self.overwrite_radio):
            layout.addWidget(r)
        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def choice(self):
        if self.rename_radio.isChecked():
            return "rename"
        if self.overwrite_radio.isChecked():
            return "overwrite"
        return "skip"

class CleanupDialog(QDialog):
    """Flag never-expanded and duplicate snippets for delete/disable."""

    def __init__(self, ui):
        super().__init__(ui)
        self.ui = ui
        self.setWindowTitle("Snippet cleanup")
        self.resize(560, 400)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            "Never expanded = no recorded usage (snippets imported before "
            "usage tracking, or never used, show here too). "
            "Duplicates = two triggers expanding to identical text."
        ))
        self.list_widget = QListWidget()
        layout.addWidget(self.list_widget)
        self._refresh()
        delete_btn = QPushButton("Delete selected (to Trash)")
        delete_btn.clicked.connect(self._delete_selected)
        disable_btn = QPushButton("Disable selected")
        disable_btn.clicked.connect(self._disable_selected)
        close_btn = QDialogButtonBox(QDialogButtonBox.Close)
        close_btn.rejected.connect(self.reject)
        row = QHBoxLayout()
        for b in (delete_btn, disable_btn, close_btn):
            row.addWidget(b)
        layout.addLayout(row)

    def _findings(self):
        if not self.ui.daemon:
            return {}
        with self.ui.daemon.snippets_lock:
            items = list(self.ui.daemon.snippets.items())
        reasons = {}
        for trigger, snippet in items:
            if isinstance(snippet, dict) and not int(snippet.get("usage", 0) or 0):
                reasons.setdefault(trigger, []).append("never expanded")
        by_text = {}
        for trigger, snippet in items:
            if not isinstance(snippet, dict):
                continue
            key = (snippet.get("type", "variable"), _normalized_expansion(snippet))
            if key[1]:
                by_text.setdefault(key, []).append(trigger)
        for triggers in by_text.values():
            if len(triggers) > 1:
                for trigger in triggers:
                    reasons.setdefault(trigger, []).append(
                        "duplicate of " + ", ".join(t for t in triggers if t != trigger)
                    )
        return reasons

    def _refresh(self):
        self.list_widget.clear()
        for trigger, reasons in sorted(self._findings().items()):
            item = QListWidgetItem(f"{trigger}  ({'; '.join(reasons)})")
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Unchecked)
            item.setData(Qt.UserRole, trigger)
            self.list_widget.addItem(item)

    def _checked(self):
        return [
            self.list_widget.item(i).data(Qt.UserRole)
            for i in range(self.list_widget.count())
            if self.list_widget.item(i).checkState() == Qt.Checked
        ]

    def _delete_selected(self):
        if not self.ui.daemon:
            return
        for trigger in self._checked():
            self.ui.daemon.delete_snippet(trigger)
        self.ui.refresh_snippet_list()
        self._refresh()

    def _disable_selected(self):
        if not self.ui.daemon:
            return
        for trigger in self._checked():
            with self.ui.daemon.snippets_lock:
                snippet = self.ui.daemon.snippets.get(trigger)
                if isinstance(snippet, dict):
                    snippet["enabled"] = False
        self.ui.daemon.save_snippets()
        self.ui.refresh_snippet_list()
        self._refresh()


class CategoryTagDialog(QDialog):
    """Rename/delete categories and tags across the whole library (item 4)."""

    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.daemon = window.daemon
        self.setWindowTitle("Manage categories & tags")
        self.setMinimumWidth(460)
        layout = QVBoxLayout(self)

        layout.addWidget(QLabel("Categories"))
        self.category_list = QListWidget()
        layout.addWidget(self.category_list)
        cat_btns = QHBoxLayout()
        self.rename_cat_btn = QPushButton("Rename category")
        self.rename_cat_btn.clicked.connect(self._rename_category)
        self.delete_cat_btn = QPushButton("Delete category")
        self.delete_cat_btn.clicked.connect(self._delete_category)
        cat_btns.addWidget(self.rename_cat_btn)
        cat_btns.addWidget(self.delete_cat_btn)
        layout.addLayout(cat_btns)

        layout.addWidget(QLabel("Tags"))
        self.tag_list = QListWidget()
        layout.addWidget(self.tag_list)
        tag_btns = QHBoxLayout()
        self.rename_tag_btn = QPushButton("Rename tag")
        self.rename_tag_btn.clicked.connect(self._rename_tag)
        self.delete_tag_btn = QPushButton("Delete tag")
        self.delete_tag_btn.clicked.connect(self._delete_tag)
        tag_btns.addWidget(self.rename_tag_btn)
        tag_btns.addWidget(self.delete_tag_btn)
        layout.addLayout(tag_btns)

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        layout.addWidget(close_btn)
        self._refresh_lists()

    def _refresh_lists(self):
        self.category_list.clear()
        for category, count in self.window.category_counts():
            item = QListWidgetItem(f"{category} ({count})")
            item.setData(Qt.UserRole, category)
            self.category_list.addItem(item)
        self.tag_list.clear()
        for tag, count in self.window.tag_counts():
            item = QListWidgetItem(f"{tag} ({count})")
            item.setData(Qt.UserRole, tag)
            self.tag_list.addItem(item)

    def _selected(self, list_widget):
        item = list_widget.currentItem()
        return item.data(Qt.UserRole) if item is not None else None

    def _after_change(self):
        self._refresh_lists()
        self.window.refresh_snippet_list()

    def _rename_category(self):
        old = self._selected(self.category_list)
        if not old:
            return
        new, ok = QInputDialog.getText(
            self, "Rename category", "New name:", QLineEdit.Normal, old
        )
        if ok and new.strip() and new.strip() != old:
            self.window.rename_category(old, new.strip())
            self._after_change()

    def _delete_category(self):
        old = self._selected(self.category_list)
        if not old:
            return
        count = dict(self.window.category_counts()).get(old, 0)
        reply = QMessageBox.question(
            self, "Delete category",
            f"Move {count} snippet(s) from '{old}' to 'uncategorized'?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if reply == QMessageBox.Yes:
            self.window.delete_category(old)
            self._after_change()

    def _rename_tag(self):
        old = self._selected(self.tag_list)
        if not old:
            return
        new, ok = QInputDialog.getText(
            self, "Rename tag", "New name:", QLineEdit.Normal, old
        )
        if ok and new.strip() and new.strip() != old:
            self.window.rename_tag(old, new.strip())
            self._after_change()

    def _delete_tag(self):
        old = self._selected(self.tag_list)
        if not old:
            return
        count = dict(self.window.tag_counts()).get(old, 0)
        reply = QMessageBox.question(
            self, "Delete tag",
            f"Remove the tag '{old}' from {count} snippet(s)?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if reply == QMessageBox.Yes:
            self.window.delete_tag(old)
            self._after_change()


class UsageStatsDialog(QDialog):
    """Library usage statistics, from stored usage/last_used data only.

    No per-day expansion log is stored, so "today" / "last 7 days"
    count snippets whose last expansion fell in that window, not
    individual expansions. Read-only: nothing here changes the library.
    """

    def __init__(self, ui):
        super().__init__(ui)
        self.setWindowTitle("Usage statistics")
        self.setMinimumWidth(420)
        daemon = getattr(ui, "daemon", None)
        with daemon.snippets_lock:
            snippets = dict(daemon.snippets) if daemon is not None else {}
        self.stats_rows = self._compute_rows(snippets, daemon)
        layout = QFormLayout(self)
        for label, value in self.stats_rows:
            layout.addRow(label + ":", QLabel(str(value)))
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        layout.addRow(close_btn)

    @staticmethod
    def _compute_rows(snippets, daemon):
        """(label, value) rows for the stats dialog."""
        total = len(snippets)
        total_uses = sum(int(sn.get("usage", 0) or 0) for sn in snippets.values())
        disabled = sum(1 for sn in snippets.values() if not sn.get("enabled", True))
        never_used = [t for t, sn in snippets.items() if int(sn.get("usage", 0) or 0) == 0]
        ranked = sorted(
            snippets.items(),
            key=lambda kv: (-int(kv[1].get("usage", 0) or 0), kv[0].lower()),
        )
        top = [f"{t} ({int(sn.get('usage', 0) or 0)}x)" for t, sn in ranked[:5]
               if int(sn.get("usage", 0) or 0) > 0]
        now = datetime.now()
        today = 0
        week = 0
        for sn in snippets.values():
            raw = sn.get("last_used") or ""
            try:
                when = datetime.fromisoformat(raw)
            except (TypeError, ValueError):
                continue
            if when.date() == now.date():
                today += 1
            if (now - when).days < 7 and when <= now:
                week += 1
        last_backup = "never"
        list_backups = getattr(daemon, "list_backups", None) if daemon is not None else None
        if callable(list_backups):
            try:
                times = [b["mtime"] for b in list_backups() if b.get("kind") == "snippets"]
                if times:
                    last_backup = datetime.fromtimestamp(max(times)).strftime("%Y-%m-%d %H:%M")
            except Exception:
                pass
        return [
            ("Snippets", total),
            ("Enabled / disabled", f"{total - disabled} / {disabled}"),
            ("Total expansions", total_uses),
            ("Most used", ", ".join(top) if top else "none yet"),
            ("Never used", f"{len(never_used)}" + (f" ({', '.join(sorted(never_used)[:5])})" if never_used else "")),
            ("Snippets used today", today),
            ("Snippets used in the last 7 days", week),
            ("Last snippets backup", last_backup),
        ]
