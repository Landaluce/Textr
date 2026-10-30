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


def create_status_icon(color_name):
    pixmap = QPixmap(16, 16)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setBrush(QColor(color_name))
    painter.setPen(Qt.NoPen)
    painter.drawEllipse(2, 2, 12, 12)
    painter.end()
    return QIcon(pixmap)


# Matches QTreeWidget background-color in stylesheet.py. The view paints a
# full-row highlight below the delegate; this erases it from the indentation
# gutter (the empty area left of the item text).
TREE_BACKGROUND = QColor("#15151c")


class SnippetTreeDelegate(QStyledItemDelegate):
    """Paints selection/hover panels clipped to the item's own cell.

    The view always paints a full-row highlight (palette-based) below the
    delegate, so the indentation gutter to the left of the text would get
    tinted too. This delegate instead draws its own panels (Catppuccin
    colors, rounded) within the cell rect and erases the view's panel from
    the gutter, so selection never covers the empty indent area.
    """

    SELECT_BG = QColor("#ff9a3c")
    SELECT_FG = QColor("#11111b")
    HOVER_BG = QColor("#2e2e38")
    RADIUS = 4

    def initStyleOption(self, option, index):
        """Force dark text on selected rows, like the table view.

        The table's selection-color (#11111b on the orange highlight)
        applies to every cell. The tree used to set that palette in
        paint() before calling super().paint(), but super() re-runs
        initStyleOption, which bakes the item's type colour back into
        the palette — so a selected row kept its red/blue/green text on
        orange. Overriding initStyleOption runs inside super().paint(),
        after that baking, so the dark colour sticks. It checks the
        view's selection model rather than option.state, because paint()
        clears State_Selected before calling super() to keep the style's
        own panel out of the indentation gutter.
        """
        super().initStyleOption(option, index)
        view = option.widget
        selected = False
        if view is not None and hasattr(view, "selectionModel"):
            try:
                selected = view.selectionModel().isSelected(index)
            except Exception:
                selected = False
        if not selected:
            selected = bool(option.state & QStyle.State_Selected)
        if selected:
            option.palette.setColor(QPalette.Text, self.SELECT_FG)
            option.palette.setColor(QPalette.HighlightedText, self.SELECT_FG)
        # Search-match highlighting: a row whose text contains the
        # manager's current search query is drawn bold, so matches stand
        # out without losing the type colour / selection colours above.
        query = getattr(option.widget, "_search_query", "") if option.widget is not None else ""
        if query:
            text = index.data(Qt.DisplayRole)
            if text and query in str(text).lower():
                font = option.font
                font.setBold(True)
                option.font = font

    def paint(self, painter, option, index):
        opt = QStyleOptionViewItem(option)
        # Keep the style's own panel from stretching into the gutter too.
        opt.showDecorationSelected = False
        panel = None
        if opt.state & QStyle.State_Selected:
            panel = self.SELECT_BG
            opt.state &= ~QStyle.State_Selected
            # Text colour is handled by initStyleOption (see above):
            # setting the palette here would be overwritten by super().
        elif opt.state & QStyle.State_MouseOver:
            panel = self.HOVER_BG
            opt.state &= ~QStyle.State_MouseOver
        if panel is not None:
            painter.save()
            painter.setRenderHint(QPainter.Antialiasing)
            path = QPainterPath()
            path.addRoundedRect(QRectF(opt.rect), self.RADIUS, self.RADIUS)
            painter.fillPath(path, panel)
            # Erase the view's full-row highlight from the gutter. Only for
            # snippet rows: group headers keep their expand arrow (drawn by
            # the view in the gutter) intact on hover.
            if index.data(Qt.UserRole + 1) is None:  # snippet row, not a group
                view = opt.widget
                if view is not None:
                    cell = view.visualRect(index)
                    if cell.x() > 0:
                        painter.fillRect(
                            QRect(0, opt.rect.top(), cell.x(), opt.rect.height()),
                            TREE_BACKGROUND,
                        )
            painter.restore()
        super().paint(painter, opt, index)


class SnippetTreeWidget(QTreeWidget):
    """Snippet tree; dropping a snippet onto a category group moves it there."""

    def contextMenuEvent(self, event):
        """Right-click menu on a snippet row: edit / duplicate / toggle / delete."""
        item = self.itemAt(event.pos())
        if item is None or item.data(0, Qt.UserRole) is None:
            return  # empty area or group header: no menu
        self.setCurrentItem(item)
        window = self.window()
        trigger = item.data(0, Qt.UserRole)
        menu = self._build_context_menu(window, trigger)
        menu.exec(event.globalPos())

    def _build_context_menu(self, window, trigger):
        """The right-click menu for a snippet row, wired to the window handlers."""
        snippet = window.daemon.snippets.get(trigger, {}) if window.daemon else {}
        enabled = snippet.get("enabled", True) if isinstance(snippet, dict) else True
        favorite = bool(snippet.get("favorite")) if isinstance(snippet, dict) else False
        menu = QMenu(self)
        menu.addAction("Edit", window.edit_snippet)
        menu.addAction("Duplicate", window.duplicate_snippet)
        menu.addAction("Unfavourite" if favorite else "Favourite", window.toggle_snippet_favorite)
        menu.addAction("Disable" if enabled else "Enable", window.toggle_snippet_enabled)
        menu.addSeparator()
        menu.addAction("Delete", window.delete_snippet)
        return menu

    def dropEvent(self, event):
        window = self.window()
        grouping = getattr(window, "group_combo", None)
        if grouping is None or grouping.currentData() != "category":
            event.ignore()
            return
        target = self.itemAt(event.position().toPoint())
        dragged = self.currentItem()
        if (
            target is None or dragged is None
            or target.data(0, Qt.UserRole) is not None  # not a group node
            or dragged.data(0, Qt.UserRole) is None     # dragged a group, not a snippet
            or target.data(0, Qt.UserRole + 2) is not None  # virtual section, not a category
        ):
            event.ignore()
            return
        group_path = self._group_path(target)
        daemon = getattr(window, "daemon", None)
        trigger = dragged.data(0, Qt.UserRole)
        if group_path and daemon:
            snippet = daemon.snippets.get(trigger)
            if isinstance(snippet, dict):
                window._push_undo(
                    f"Move '{trigger}' to '{'/'.join(group_path)}'"
                )
                snippet["category"] = "/".join(group_path)
                daemon.save_snippets()
                window.refresh_snippet_list()
                event.accept()
                return
        event.ignore()

    def _group_path(self, item):
        parts = []
        node = item
        while node is not None:
            name = node.data(0, Qt.UserRole + 1)
            if name is None:
                break
            parts.append(name)
            node = node.parent()
        return list(reversed(parts))

    def find_item(self, trigger):
        """Return the tree item for a snippet trigger, or None."""
        def walk(item):
            if item.data(0, Qt.UserRole) == trigger:
                return item
            for i in range(item.childCount()):
                found = walk(item.child(i))
                if found:
                    return found
            return None

        for i in range(self.topLevelItemCount()):
            found = walk(self.topLevelItem(i))
            if found:
                return found
        return None


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
        builtins = set(TexterUI.BUILTIN_PLACEHOLDERS) | {"cursor"}
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


class UsedByTableWidgetItem(QTableWidgetItem):
    """Used by cell that sorts by (snippet count, use count), not text.

    The displayed text is e.g. "3 snippets, 4 uses: ..." or "0 snippets",
    which would sort wrong as plain text ("10 ..." before "2 ...").
    refresh_user_setting_usage() stores the numeric pair in UserRole;
    this __lt__ compares that pair, falling back to text.
    """

    def __lt__(self, other):
        self_key = self.data(Qt.ItemDataRole.UserRole)
        other_key = other.data(Qt.ItemDataRole.UserRole)
        if isinstance(self_key, tuple) and isinstance(other_key, tuple):
            return self_key < other_key
        return super().__lt__(other)


class SortableTableWidgetItem(QTableWidgetItem):
    """Table cell that sorts by a stored UserRole key, not its text.

    Text columns store a lowercased key (so ";Sig" and ";sig" sort
    together), Uses stores an int (so 10 sorts above 2), and Last used
    stores the raw ISO timestamp ("" for never, so never-used rows sort
    first ascending / last descending). Falls back to plain text sort
    when either side has no stored key.
    """

    def __lt__(self, other):
        self_key = self.data(Qt.ItemDataRole.UserRole)
        other_key = other.data(Qt.ItemDataRole.UserRole)
        if self_key is not None and other_key is not None:
            try:
                return self_key < other_key
            except TypeError:
                pass
        return super().__lt__(other)


class SnippetTableWidget(QTableWidget):
    """Snippet table; right-click menu mirrors the tree's menu."""

    def contextMenuEvent(self, event):
        item = self.itemAt(event.pos())
        if item is None:
            return
        trigger_item = self.item(item.row(), 0)
        if trigger_item is None or trigger_item.data(Qt.UserRole + 1) is None:
            return
        self.setCurrentCell(item.row(), 0)
        window = self.window()
        trigger = trigger_item.data(Qt.UserRole + 1)
        snippet = window.daemon.snippets.get(trigger, {}) if window.daemon else {}
        enabled = snippet.get("enabled", True) if isinstance(snippet, dict) else True
        favorite = bool(snippet.get("favorite")) if isinstance(snippet, dict) else False
        menu = QMenu(self)
        menu.addAction("Edit", window.edit_snippet)
        menu.addAction("Duplicate", window.duplicate_snippet)
        menu.addAction("Unfavourite" if favorite else "Favourite", window.toggle_snippet_favorite)
        menu.addAction("Disable" if enabled else "Enable", window.toggle_snippet_enabled)
        menu.addSeparator()
        menu.addAction("Delete", window.delete_snippet)
        menu.exec(event.globalPos())



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


def _fuzzy_score(trigger, expansion, query):
    """Score for the quick picker: substring beats subsequence beats none."""
    if not query:
        return 1
    t = trigger.lower()
    e = (expansion or "").lower()
    if query in t:
        return 100 - (len(t) - len(query))
    if query in e:
        return 60
    # Subsequence match in the trigger (e.g. "sig" in "signature").
    it = iter(t)
    if all(ch in it for ch in query):
        return 40
    return 0


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
        item.setForeground(QColor("#ff9a3c"))
        self.list_widget.addItem(item)
        return item

    def _refresh_list(self):
        query = self.search.text().strip().lower()
        scored = []
        for trigger, snippet, raw in self._candidates():
            score = _fuzzy_score(trigger, raw, query)
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
            history = [TexterUI._history_entry(current)]
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


def _normalized_expansion(snippet):
    """Comparable expansion text for duplicate detection."""
    kind = snippet.get("type", "variable")
    if kind == "variable":
        text = snippet.get("value", "")
    elif kind == "template":
        languages = snippet.get("languages", {})
        text = " | ".join(
            f"{lang}:{value}"
            for lang, value in sorted(languages.items())
            if isinstance(value, str)
        ) if isinstance(languages, dict) else ""
    elif kind == "command":
        text = str(snippet.get("action", ""))
    else:
        text = ""
    return " ".join(str(text).split())


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
        self.really_quit = True
        if self.daemon:
            self.daemon.save_snippets()
            self.daemon.stop()
        if hasattr(self, 'tray_icon'):
            self.tray_icon.hide()
        self.close()
        QApplication.quit()

    def closeEvent(self, event):
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

    SETTING_PLACEHOLDER_RE = re.compile(
        r"(?<![\{$])\{([A-Za-z_][A-Za-z0-9_]*)\}(?!\})"
    )
    # Built-ins from daemon.get_context() (name, email, date, time, iso, week)
    # plus clipboard from daemon.render_template(). These always win over a
    # custom setting of the same name, so they must stay reserved.
    BUILTIN_PLACEHOLDERS = frozenset(
        {"name", "email", "date", "time", "iso", "week", "clipboard"}
    )

    def add_user_setting_row(self, key="", value=""):
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
                    f"Row {row + 1}: use a setting name such as company or first_name."
                )
            if key in self.BUILTIN_PLACEHOLDERS:
                raise ValueError(f"'{key}' is reserved for a built-in placeholder.")
            if key in settings:
                raise ValueError(f"Duplicate custom setting: '{key}'.")
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
                    f"Added {len(added)} new setting(s).\n"
                    f"{len(unset)} referenced setting(s) have no value:\n\n"
                    + ", ".join(unset)
                    + "\n\nFill in their values, then click Save Settings."
                )
            else:
                message = "All detected custom placeholders already have values."
            QMessageBox.information(self, "Snippet settings", message)
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
        snap = {
            k: deepcopy(v) for k, v in (snippet or {}).items() if k != "history"
        }
        return {
            "saved_at": datetime.now().isoformat(timespec="seconds"),
            "snippet": snap,
        }

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
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Search triggers or expansion text...")
        self.search_input.textChanged.connect(self.refresh_snippet_list)
        left_layout.addWidget(self.search_input)

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
        self.preview_text = QTextEdit()
        self.preview_text.setReadOnly(True)
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

        # User settings panel
        settings_group = QGroupBox("User Profile Settings")
        self.settings_group = settings_group
        settings_layout = QFormLayout(settings_group)
        self.name_edit = QLineEdit(self.daemon.config.get("name", "") if (self.daemon and hasattr(self.daemon, "config")) else "")
        self.email_edit = QLineEdit(self.daemon.config.get("email", "") if (self.daemon and hasattr(self.daemon, "config")) else "")
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
        settings_layout.addRow("Name:", self.name_edit)
        settings_layout.addRow("Email:", self.email_edit)
        settings_layout.addRow("Keyboard layout:", self.layout_combo)
        settings_layout.addRow("Paste shortcut:", self.paste_hotkey_edit)
        settings_layout.addRow("Date format:", self.date_format_edit)
        settings_layout.addRow("Time format:", self.time_format_edit)
        self.picker_hotkey_edit = QLineEdit(
            self.daemon.config.get("picker_hotkey", "ctrl+alt+space")
            if (self.daemon and hasattr(self.daemon, "config")) else "ctrl+alt+space"
        )
        self.picker_hotkey_edit.setPlaceholderText("ctrl+alt+space")
        self.picker_hotkey_edit.setToolTip(
            "Global shortcut for Quick Insert. Applies when you click Save "
            "Settings (X11/pynput only; also takes effect on next start)."
        )
        settings_layout.addRow("Quick Insert shortcut:", self.picker_hotkey_edit)
        self.close_behavior_combo = QComboBox()
        self.close_behavior_combo.addItem("Quit Textr", "quit")
        self.close_behavior_combo.addItem("Minimize to tray", "tray")
        _close_idx = self.close_behavior_combo.findData(
            self.daemon.config.get("close_behavior", "quit")
            if (self.daemon and hasattr(self.daemon, "config")) else "quit"
        )
        self.close_behavior_combo.setCurrentIndex(max(_close_idx, 0))
        settings_layout.addRow("Close window:", self.close_behavior_combo)
        self.autostart_check = QCheckBox("Start Textr when I log in (Linux)")
        self.autostart_check.blockSignals(True)
        self.autostart_check.setChecked(self._autostart_enabled())
        self.autostart_check.blockSignals(False)
        self.autostart_check.toggled.connect(self._on_autostart_toggled)
        settings_layout.addRow(self.autostart_check)
        self.picker_hotkey_edit.textChanged.connect(self._update_settings_dirty)
        self.close_behavior_combo.currentIndexChanged.connect(self._update_settings_dirty)
        self.user_settings_table = QTableWidget(0, 3)
        self.user_settings_table.setHorizontalHeaderLabels(
            ["Setting", "Value", "Used by"]
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
        settings_layout.addRow("Custom settings:", self.user_settings_table)

        custom_buttons = QHBoxLayout()
        self.add_setting_btn = QPushButton("Add setting")
        self.add_setting_btn.clicked.connect(self.add_user_setting)
        self.remove_setting_btn = QPushButton("Remove selected")
        self.remove_setting_btn.clicked.connect(self.remove_user_settings)
        custom_buttons.addWidget(self.add_setting_btn)
        custom_buttons.addWidget(self.remove_setting_btn)
        settings_layout.addRow(custom_buttons)

        self.find_missing_settings_btn = QPushButton("Find missing settings")
        self.find_missing_settings_btn.clicked.connect(self.find_missing_user_settings)
        settings_layout.addRow(self.find_missing_settings_btn)
        hint = QLabel(
            "Double-click Setting/Value cells to edit. The Used by column shows "
            "how many snippets use each setting and which ones. Missing "
            "{setting_name} placeholders are added automatically on startup and "
            "after imports. Fill in values and click Save Settings."
        )
        hint.setWordWrap(True)
        settings_layout.addRow(hint)

        config = self.daemon.config if self.daemon else {}
        custom_settings = config.get("user_settings", {})
        if isinstance(custom_settings, dict):
            for key, value in custom_settings.items():
                self.add_user_setting_row(str(key), str(value))

        settings_layout.addRow(self.save_settings_btn)
        # Collapsible settings (declutter): the preview stays visible
        # and the profile/custom-settings block folds away with one
        # click; its state is saved with the window state.
        self.settings_toggle_btn = QPushButton("\u25be Profile & custom settings")
        self.settings_toggle_btn.setCheckable(True)
        self.settings_toggle_btn.setChecked(True)
        self.settings_toggle_btn.setToolTip(
            "Show or hide the profile and custom settings; the preview above stays visible"
        )
        self.settings_toggle_btn.toggled.connect(self._on_settings_toggle)
        right_layout.addWidget(self.settings_toggle_btn)
        right_layout.addWidget(settings_group)


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

    def _on_settings_toggle(self, checked):
        """Fold/unfold the profile & custom-settings block."""
        if hasattr(self, "settings_group"):
            self.settings_group.setVisible(bool(checked))
        self._sync_settings_toggle_text()

    def _sync_settings_toggle_text(self):
        """Toggle label: fold arrow, plus a bullet when settings are dirty."""
        if not hasattr(self, "settings_toggle_btn"):
            return
        arrow = "\u25be" if self.settings_toggle_btn.isChecked() else "\u25b8"
        dirty = False
        if getattr(self, "_settings_baseline", None) is not None:
            try:
                dirty = self._settings_snapshot() != self._settings_baseline
            except Exception:
                dirty = False
        bullet = " \u2022" if dirty else ""
        self.settings_toggle_btn.setText(f"{arrow} Profile & custom settings{bullet}")

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

    def _save_window_state(self):
        """Persist geometry, view/group/sort, and column widths (item 1)."""
        settings = self._app_settings
        settings.setValue("geometry", self.saveGeometry())
        settings.setValue("view", self.view_combo.currentData())
        settings.setValue("group", self.group_combo.currentData())
        settings.setValue("subgroup", self.subgroup_combo.currentData())
        settings.setValue("sort", self.sort_combo.currentData())
        settings.setValue(
            "tableColumnWidths",
            [self.snippet_table.columnWidth(c) for c in range(self.snippet_table.columnCount())],
        )
        settings.setValue(
            "settingsColumnWidths",
            [self.user_settings_table.columnWidth(c) for c in range(self.user_settings_table.columnCount())],
        )
        settings.setValue(
            "settingsExpanded",
            bool(self.settings_toggle_btn.isChecked())
            if hasattr(self, "settings_toggle_btn") else True,
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
        _expanded = settings.value("settingsExpanded")
        if _expanded is not None and hasattr(self, "settings_toggle_btn"):
            _checked = (
                bool(_expanded)
                if isinstance(_expanded, bool)
                else str(_expanded).lower() == "true"
            )
            self.settings_toggle_btn.blockSignals(True)
            self.settings_toggle_btn.setChecked(_checked)
            self.settings_toggle_btn.blockSignals(False)
            if hasattr(self, "settings_group"):
                self.settings_group.setVisible(_checked)
            self._sync_settings_toggle_text()
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
        self._sync_settings_toggle_text()
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
            # resolvable: orange, never the missing red.
            color = "#ff9a3c" if name is None or name not in missing else "#f38ba8"
            return f'<b><font color="{color}">{html.escape(match.group(0))}</font></b>'

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
            self.preview_missing_label.setText(
                "Missing settings: " + ", ".join(f"{{{n}}}" for n in missing)
                + " - no value set; expansion will prompt for them."
            )
            self.preview_missing_label.setStyleSheet("color: #f38ba8;")
        elif hasattr(self, "preview_missing_label"):
            self.preview_missing_label.setText("")
            self.preview_missing_label.setStyleSheet("")
        if hasattr(self, "preview_mode_btn"):
            self.preview_mode_btn.setText(
                "Show rendered" if self._preview_mode == "raw" else "Show raw text"
            )

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
                if _query and _query in str(text).lower():
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

        query = self.search_input.text().strip().lower()
        items = [
            (trigger, snippet)
            for trigger, snippet in snippets.items()
            if self._matches_search(trigger, snippet, query)
            and self._matches_quick_filter(trigger, snippet)
        ]
        self._sort_items(items)
        # The tree delegate reads this for search-match bolding (item 2).
        self.snippet_list._search_query = query
        self._update_filter_status(len(items), len(snippets))
        self._last_filtered_items = list(items)
        self._update_health_status()

        if self._is_table_view():
            self._refresh_snippet_table(items, selected_before)
            self.refresh_user_setting_usage()
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

    def _matches_search(self, trigger, snippet, query):
        """True if the trigger, tags, or any expansion text contains the query."""
        if not query:
            return True
        if query in trigger.lower():
            return True
        tags = " ".join(snippet.get("tags") or []).lower()
        if query in tags:
            return True
        if query in str(snippet.get("note", "")).lower():
            return True
        snippet_type = snippet.get("type", "variable")
        if snippet_type == "variable":
            haystack = snippet.get("value", "")
        elif snippet_type == "template":
            haystack = " ".join(str(v) for v in snippet.get("languages", {}).values())
        elif snippet_type == "command":
            haystack = snippet.get("action", "")
        else:
            haystack = ""
        return query in haystack.lower()

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
