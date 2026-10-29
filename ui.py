# texter_mvp/ui.py
import sys
import json
import threading
import re
from copy import deepcopy
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QTextEdit, QPushButton, QComboBox,
    QListWidget, QListWidgetItem, QDialog, QFormLayout,
    QMessageBox, QFileDialog, QGroupBox, QSystemTrayIcon, QMenu,
    QCheckBox, QTreeWidget, QTreeWidgetItem, QAbstractItemView,
    QStyledItemDelegate, QStyle, QStyleOptionViewItem,
    QTableWidget, QTableWidgetItem, QHeaderView
)
from PySide6.QtCore import Qt, QTimer, Signal, QRectF, QRect
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
TREE_BACKGROUND = QColor("#313244")


class SnippetTreeDelegate(QStyledItemDelegate):
    """Paints selection/hover panels clipped to the item's own cell.

    The view always paints a full-row highlight (palette-based) below the
    delegate, so the indentation gutter to the left of the text would get
    tinted too. This delegate instead draws its own panels (Catppuccin
    colors, rounded) within the cell rect and erases the view's panel from
    the gutter, so selection never covers the empty indent area.
    """

    SELECT_BG = QColor("#b4befe")
    SELECT_FG = QColor("#11111b")
    HOVER_BG = QColor("#45475a")
    RADIUS = 4

    def paint(self, painter, option, index):
        opt = QStyleOptionViewItem(option)
        # Keep the style's own panel from stretching into the gutter too.
        opt.showDecorationSelected = False
        panel = None
        if opt.state & QStyle.State_Selected:
            panel = self.SELECT_BG
            opt.state &= ~QStyle.State_Selected
            # Dark text over the light highlight; the item's foreground role
            # (type color) is baked into palette Text by initStyleOption.
            opt.palette.setColor(QPalette.Text, self.SELECT_FG)
            opt.palette.setColor(QPalette.HighlightedText, self.SELECT_FG)
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
        menu = QMenu(self)
        menu.addAction("Edit", window.edit_snippet)
        menu.addAction("Duplicate", window.duplicate_snippet)
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
        ):
            event.ignore()
            return
        group_path = self._group_path(target)
        daemon = getattr(window, "daemon", None)
        trigger = dragged.data(0, Qt.UserRole)
        if group_path and daemon:
            snippet = daemon.snippets.get(trigger)
            if isinstance(snippet, dict):
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
    def __init__(self, parent=None, snippet=None, categories=None):
        super().__init__(parent)
        self.snippet = snippet
        self.categories = categories or []
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

        # Type
        self.type_combo = QComboBox()
        self.type_combo.addItems(["variable", "template", "command"])
        self.type_combo.currentTextChanged.connect(self.on_type_changed)
        layout.addRow("Type:", self.type_combo)

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

    def on_type_changed(self, type_val):
        self.languages_input.setHidden(type_val != "template")
        self.action_input.setHidden(type_val != "command")
        self.command_value_input.setHidden(type_val != "command")

    def get_snippet(self):
        trigger = self.trigger_input.text().strip()
        snippet_type = self.type_combo.currentText()
        category = self.category_input.currentText().strip() or "uncategorized"
        raw_tags = [t.strip() for t in self.tags_input.text().split(",") if t.strip()]

        snippet = {"trigger": trigger, "type": snippet_type, "category": category}
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


class TexterUI(QMainWindow):
    quit_requested = Signal()
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
        self.quit_requested.connect(self.quit_app)
        self.daemon_command.connect(self.handle_daemon_command, Qt.QueuedConnection)
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
        if hasattr(self, 'really_quit') and self.really_quit:
            event.accept()
        else:
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

    def remove_user_settings(self):
        rows = {
            index.row()
            for index in self.user_settings_table.selectionModel().selectedRows()
        }
        for row in sorted(rows, reverse=True):
            self.user_settings_table.removeRow(row)

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
        left_layout.addLayout(cat_layout)

        # Search box
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Search triggers or expansion text...")
        self.search_input.textChanged.connect(self.refresh_snippet_list)
        left_layout.addWidget(self.search_input)

        # Grouping and sort controls
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
        view_layout.addWidget(QLabel("Then by:"))
        self.subgroup_combo = QComboBox()
        self.subgroup_combo.addItem("None", "none")
        self.subgroup_combo.addItem("Category", "category")
        self.subgroup_combo.addItem("Type", "type")
        self.subgroup_combo.addItem("Tag", "tag")
        self.subgroup_combo.setCurrentIndex(self.subgroup_combo.findData("tag"))
        self.subgroup_combo.currentIndexChanged.connect(self.refresh_snippet_list)
        view_layout.addWidget(self.subgroup_combo)
        left_layout.addLayout(view_layout)

        sort_layout = QHBoxLayout()
        sort_layout.addWidget(QLabel("Sort:"))
        self.sort_combo = QComboBox()
        self.sort_combo.addItem("Name", "name")
        self.sort_combo.addItem("Recently used", "recent")
        self.sort_combo.addItem("Most used", "used")
        self.sort_combo.currentIndexChanged.connect(self.refresh_snippet_list)
        sort_layout.addWidget(self.sort_combo)
        left_layout.addLayout(sort_layout)

        # Snippet tree (groupable list); font comes from the QSS stylesheet.
        self.snippet_list = SnippetTreeWidget()
        self.snippet_list.setHeaderHidden(True)
        self.snippet_list.setItemDelegate(SnippetTreeDelegate(self.snippet_list))
        self.snippet_list.setDragDropMode(QAbstractItemView.InternalMove)
        self.snippet_list.itemSelectionChanged.connect(self.on_selection_changed)
        left_layout.addWidget(self.snippet_list)

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
        btn_layout.addWidget(self.add_btn)
        btn_layout.addWidget(self.edit_btn)
        btn_layout.addWidget(self.delete_btn)
        left_layout.addLayout(btn_layout)

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

        # Preview area
        preview_layout = QHBoxLayout()
        preview_layout.addWidget(QLabel("Template Preview:"))
        self.preview_text = QTextEdit()
        self.preview_text.setReadOnly(True)
        self.preview_text.setPlaceholderText("Select a template snippet to preview")
        preview_layout.addWidget(self.preview_text)
        right_layout.addLayout(preview_layout)

        # User settings panel
        settings_group = QGroupBox("User Profile Settings")
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
        settings_layout.addRow("Name:", self.name_edit)
        settings_layout.addRow("Email:", self.email_edit)
        settings_layout.addRow("Keyboard layout:", self.layout_combo)
        settings_layout.addRow("Paste shortcut:", self.paste_hotkey_edit)
        settings_layout.addRow("Date format:", self.date_format_edit)
        settings_layout.addRow("Time format:", self.time_format_edit)
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
        right_layout.addWidget(settings_group)


        # Buttons
        action_layout = QHBoxLayout()

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

        self.quit_btn = QPushButton("Quit")
        self.quit_btn.clicked.connect(self.quit_button)

        self.import_btn = QPushButton("Import JSON")
        self.import_btn.clicked.connect(self.import_json)

        self.export_btn = QPushButton("Export JSON")
        self.export_btn.clicked.connect(self.export_json)

        action_layout.addWidget(self.toggle_daemon_btn)
        action_layout.addWidget(self.toggle_writing_btn)
        action_layout.addWidget(self.quit_btn)
        action_layout.addWidget(self.import_btn)
        action_layout.addWidget(self.export_btn)
        right_layout.addLayout(action_layout)

        main_layout.addWidget(right_group, 2)

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

        self.refresh_snippet_list()
        self.find_missing_user_settings(notify=False)

    def get_expansion_preview(self, snippet):
        if not self.daemon:
            return ""
        snippet_type = snippet.get("type", "variable")
        if snippet_type == "variable":
            return snippet.get("value", "")
        elif snippet_type == "template":
            current_lang = self.language_combo.currentText()
            raw = snippet.get("languages", {}).get(current_lang)
            if not raw:
                return f"(No template for {current_lang})"
            return self.daemon.render_template(raw)
        elif snippet_type == "command":
            return f"Command: {snippet.get('action')}"
        return ""

    def refresh_snippet_list(self):
        # Remember the selection so rebuilds (search, language switch, daemon
        # refresh) don't drop it - the user shouldn't have to re-click.
        selected_before = None
        selected = self.snippet_list.selectedItems()
        if selected:
            selected_before = selected[0].data(0, Qt.UserRole)

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
        ]
        self._sort_items(items)

        group_mode = self.group_combo.currentData()
        sub_mode = self.subgroup_combo.currentData()
        if group_mode == "none":
            for trigger, snippet in items:
                self._add_snippet_item(self.snippet_list, trigger, snippet)
        else:
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

    def _ensure_group(self, parent, name):
        """Find or create a (non-selectable) group node named `name`.

        `parent` may be the tree widget itself (root level) or another group
        item (nested folders).
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
        item.setFlags(item.flags() & ~Qt.ItemIsSelectable)
        # Bold lavender headers (Catppuccin Mocha) so groups stand out.
        item.setForeground(0, QColor("#cba6f7"))
        group_font = item.font(0)
        group_font.setBold(True)
        item.setFont(0, group_font)
        return item

    # Light Catppuccin Mocha accents, readable on the dark background.
    TYPE_COLORS = {
        "command": "#f38ba8",   # red
        "template": "#89b4fa",  # blue
        "variable": "#a6e3a1",  # green
    }
    DISABLED_COLOR = "#6c7086"  # muted Catppuccin overlay for disabled snippets

    def _add_snippet_item(self, parent, trigger, snippet):
        item = QTreeWidgetItem(parent, [trigger])
        item.setData(0, Qt.UserRole, trigger)
        snippet_type = snippet.get("type", "variable")
        category = snippet.get("category", "uncategorized")
        enabled = snippet.get("enabled", True)
        item.setForeground(
            0, QColor(self.DISABLED_COLOR if not enabled else self.TYPE_COLORS.get(snippet_type, "#cdd6f4"))
        )
        state = " | DISABLED" if not enabled else ""
        item.setToolTip(0, f"{snippet_type.capitalize()} | Category: {category}{state}")
        return item

    def on_category_changed(self, category):
        """Handle category dropdown change"""
        if self.daemon:
            self.daemon.set_category(category)
        self.refresh_snippet_list()
        print(f"UI: Showing {category} snippets")

    def on_selection_changed(self):
        selected = self.snippet_list.selectedItems()
        if not selected or not self.daemon:
            self.preview_text.clear()
            return

        trigger = selected[0].data(0, Qt.UserRole)
        if not trigger:  # a group header, not a snippet
            self.preview_text.clear()
            return
        snippet = self.daemon.snippets.get(trigger)
        if not snippet:
            self.preview_text.clear()
            return

        preview = self.get_expansion_preview(snippet)
        snippet_category = snippet.get("category", "uncategorized")
        snippet_type = snippet.get("type", "variable")
        tags = snippet.get("tags") or []
        used = int(snippet.get("usage", 0))
        last_used = snippet.get("last_used", "never")
        disabled_note = "\n\n*** DISABLED - this snippet will not expand ***" if not snippet.get("enabled", True) else ""

        self.preview_text.setText(
            f"Category: {snippet_category}\nTags: {', '.join(tags) if tags else 'none'}\n"
            f"Type: {snippet_type}\nTrigger: {trigger}\n"
            f"Used: {used} times (last: {last_used})\n\nExpands to:\n{preview}{disabled_note}"
        )

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
        dialog = AddSnippetDialog(self, categories=categories)
        if dialog.exec() == QDialog.Accepted:
            snippet = dialog.get_snippet()
            if snippet and self.daemon:
                trigger = snippet.pop("trigger")
                if not self._confirm_trigger_conflicts(trigger):
                    return
                self.daemon.add_or_update_snippet(trigger, snippet)
                self.refresh_snippet_list()
                print(f"UI: Added snippet '{trigger}' (saved)")

    def edit_snippet(self):
        selected = self.snippet_list.selectedItems()
        if not selected or not self.daemon:
            return

        trigger = selected[0].data(0, Qt.UserRole)
        if not trigger:  # group headers aren't editable
            return
        snippet = {"trigger": trigger, **self.daemon.snippets.get(trigger, {})}

        categories = self.daemon.get_categories() if self.daemon else []
        dialog = AddSnippetDialog(self, snippet=snippet, categories=categories)
        if dialog.exec() == QDialog.Accepted:
            new_snippet = dialog.get_snippet()
            if new_snippet and self.daemon:
                new_trigger = new_snippet.pop("trigger")
                if not self._confirm_trigger_conflicts(new_trigger, exclude=trigger):
                    return
                self.daemon.replace_snippet(trigger, new_trigger, new_snippet)
                self.refresh_snippet_list()
                print(f"UI: Edited snippet '{new_trigger}' (saved)")

    def duplicate_snippet(self):
        """Clone the selected snippet into a pre-filled Add dialog."""
        selected = self.snippet_list.selectedItems()
        if not selected or not self.daemon:
            return

        trigger = selected[0].data(0, Qt.UserRole)
        if not trigger:  # group headers aren't duplicable
            return
        source = dict(self.daemon.snippets.get(trigger, {}))
        # Pre-fill everything except the trigger so the user types a new one.
        dialog = AddSnippetDialog(self, snippet=source, categories=self.daemon.get_categories())
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
                self.daemon.add_or_update_snippet(new_trigger, new_snippet)
                self.refresh_snippet_list()
                print(f"UI: Duplicated snippet '{trigger}' as '{new_trigger}' (saved)")

    def toggle_snippet_enabled(self):
        """Flip the enabled flag of the selected snippet and persist it."""
        selected = self.snippet_list.selectedItems()
        if not selected or not self.daemon:
            return

        trigger = selected[0].data(0, Qt.UserRole)
        if not trigger:  # group headers aren't snippets
            return
        snippet = dict(self.daemon.snippets.get(trigger, {}))
        snippet["enabled"] = not snippet.get("enabled", True)
        self.daemon.add_or_update_snippet(trigger, snippet)
        self.refresh_snippet_list()
        state = "Disabled" if not snippet["enabled"] else "Enabled"
        print(f"UI: {state} snippet '{trigger}' (saved)")

    def delete_snippet(self):
        selected = self.snippet_list.selectedItems()
        if not selected or not self.daemon:
            return

        trigger = selected[0].data(0, Qt.UserRole)
        if not trigger:  # group headers aren't deletable
            return
        reply = QMessageBox.question(self, "Confirm", f"Delete '{trigger}'?")
        if reply == QMessageBox.Yes:
            self.daemon.delete_snippet(trigger)
            self.refresh_snippet_list()
            print(f"UI: Deleted snippet '{trigger}' (saved)")

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

    def quit_button(self):
        """Button handler for manual quit.

        Same code path as the window's X button (closeEvent) and the tray
        Quit action: quit_app() saves snippets, stops the daemon, hides the
        tray icon, and exits the application.
        """
        self.quit_app()

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
