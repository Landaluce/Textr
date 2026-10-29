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
    QTableWidget, QTableWidgetItem, QHeaderView, QDialogButtonBox,
    QRadioButton
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
        menu = QMenu(self)
        menu.addAction("Edit", window.edit_snippet)
        menu.addAction("Duplicate", window.duplicate_snippet)
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

    def __init__(self, keys):
        super().__init__(None)
        self.setWindowTitle("Fill in snippet values")
        self.setWindowFlags(
            self.windowFlags() | Qt.WindowStaysOnTopHint | Qt.Tool
        )
        self._edits = {}
        layout = QVBoxLayout(self)
        form = QFormLayout()
        for key in keys:
            edit = QLineEdit()
            edit.setPlaceholderText(f"Value for {{{key}}}")
            self._edits[key] = edit
            form.addRow(f"{key}:", edit)
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
        return {k: e.text() for k, e in self._edits.items()}

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
        for _neg_score, _neg_usage, trigger, snippet, raw in scored:
            preview = " ".join(str(raw).split())[:80]
            item = QListWidgetItem(
                f"{trigger}  [{snippet.get('type', '')}]  {preview}"
            )
            item.setData(Qt.UserRole, trigger)
            self.list_widget.addItem(item)
        if self.list_widget.count():
            self.list_widget.setCurrentRow(0)
            self.list_widget.item(0).setSelected(True)

    def _on_accept(self):
        item = self.list_widget.currentItem()
        if item is None:
            selected = self.list_widget.selectedItems()
            item = selected[0] if selected else None
        if item is None and self.list_widget.count():
            # OK pressed with no selection (e.g. selection cleared by
            # clicking empty list space): insert the top result instead
            # of silently doing nothing. Double-click never hits this
            # because it always carries its own item.
            item = self.list_widget.item(0)
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
        values = {}
        if missing:
            values = self.ui.prompt_for_values_sync(missing)
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
        btn_row = QHBoxLayout()
        btn_row.addWidget(restore_btn)
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

    # ---- Prompt-on-expand ---------------------------------------------
    def _prompt_from_daemon_thread(self, missing):
        """Prompt handler called from the daemon expansion worker.

        Shows the dialog on the GUI thread via a queued signal and waits
        for it. Returns the values dict, or None on cancel/timeout.
        """
        self._prompt_event = threading.Event()
        self._prompt_result = None
        self.prompt_requested.emit(list(missing))
        if not self._prompt_event.wait(timeout=120):
            return None
        return self._prompt_result

    def _handle_prompt_request(self, missing):
        # Lower the manager so it does not steal focus back when the
        # prompt dialog closes; xdotool restore below is the exact fix.
        try:
            self.lower()
        except Exception:
            pass
        dialog = PromptValuesDialog(missing)
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

    def prompt_for_values_sync(self, missing):
        """Prompt on the GUI thread (quick picker path). None = cancelled."""
        dialog = PromptValuesDialog(missing)
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
                        if k not in ("usage", "last_used")}
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
        sort_layout.addWidget(QLabel("View:"))
        self.view_combo = QComboBox()
        self.view_combo.addItem("Tree", "tree")
        self.view_combo.addItem("Table", "table")
        self.view_combo.setCurrentIndex(self.view_combo.findData("table"))
        self.view_combo.currentIndexChanged.connect(self._on_view_changed)
        sort_layout.addWidget(self.view_combo)
        left_layout.addLayout(sort_layout)

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
        self.snippet_table.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
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
        left_layout.addWidget(self.snippet_table)
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
        # Same edit/delete shortcuts, scoped to the table so they work
        # when the table (not the tree) has focus.
        for seq in (QKeySequence("F2"), QKeySequence(Qt.Key_Return), QKeySequence(Qt.Key_Enter)):
            shortcut = QShortcut(seq, self.snippet_table)
            shortcut.setContext(Qt.WidgetWithChildrenShortcut)
            shortcut.activated.connect(self.edit_snippet)
        self.delete_shortcut_table = QShortcut(QKeySequence(Qt.Key_Delete), self.snippet_table)
        self.delete_shortcut_table.setContext(Qt.WidgetWithChildrenShortcut)
        self.delete_shortcut_table.activated.connect(self.delete_snippet)

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

        # Primary action row: Quick Insert only.
        primary_layout = QHBoxLayout()
        self.picker_btn = QPushButton("Quick Insert (Ctrl+Alt+Space)")
        self.picker_btn.setObjectName("primary_btn")
        self.picker_btn.clicked.connect(self.show_picker)
        primary_layout.addWidget(self.picker_btn)
        right_layout.addLayout(primary_layout)

        # Secondary row: Library menu + maintenance tools.
        library_layout = QHBoxLayout()
        self.library_menu = QMenu(self)
        self.library_menu.addAction("Import JSON", self.import_json)
        self.library_menu.addAction("Export JSON", self.export_json)
        self.library_menu.addSeparator()
        self.library_menu.addAction("Import Pack", self.import_pack)
        self.library_menu.addAction("Export Pack", self.export_pack)
        self.library_btn = QPushButton("Library")
        self.library_btn.setMenu(self.library_menu)
        library_layout.addWidget(self.library_btn)
        self.backups_btn = QPushButton("Backups")
        self.backups_btn.clicked.connect(self.show_backups)
        self.trash_btn = QPushButton("Trash")
        self.trash_btn.clicked.connect(self.show_trash)
        self.cleanup_btn = QPushButton("Cleanup")
        self.cleanup_btn.clicked.connect(self.show_cleanup)
        for b in (self.backups_btn, self.trash_btn, self.cleanup_btn):
            library_layout.addWidget(b)
        right_layout.addLayout(library_layout)

        # Snippets and Settings panels share the window 50/50
        # (left_group already has stretch 1, so this matches it).
        main_layout.addWidget(right_group, 1)

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

    def _is_table_view(self):
        """True when the Table view (not the Tree view) is active."""
        view = getattr(self, "view_combo", None)
        return view is not None and view.currentData() == "table"

    def _apply_snippet_view(self):
        """Show the active snippet view; grouping only applies to the tree."""
        is_table = self._is_table_view()
        self.snippet_list.setVisible(not is_table)
        self.snippet_table.setVisible(is_table)
        self.group_combo.setEnabled(not is_table)
        self.subgroup_combo.setEnabled(
            not is_table and self.group_combo.currentData() != "none"
        )

    def _on_view_changed(self):
        """View switcher: show the chosen view and rebuild it."""
        self._apply_snippet_view()
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
                cell = SortableTableWidgetItem(text)
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
        ]
        self._sort_items(items)

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
        # Bold orange headers (Daily Alvaro briefing) so groups stand out.
        item.setForeground(0, QColor("#ff9a3c"))
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
    DISABLED_COLOR = "#9a9aa5"  # briefing muted for disabled snippets

    def _add_snippet_item(self, parent, trigger, snippet):
        item = QTreeWidgetItem(parent, [trigger])
        item.setData(0, Qt.UserRole, trigger)
        snippet_type = snippet.get("type", "variable")
        category = snippet.get("category", "uncategorized")
        enabled = snippet.get("enabled", True)
        item.setForeground(
            0, QColor(self.DISABLED_COLOR if not enabled else self.TYPE_COLORS.get(snippet_type, "#d6d6de"))
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
        trigger = self.selected_trigger()
        if not trigger or not self.daemon:
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
        trigger = self.selected_trigger()
        if not trigger or not self.daemon:
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
        trigger = self.selected_trigger()
        if not trigger or not self.daemon:
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
        trigger = self.selected_trigger()
        if not trigger or not self.daemon:
            return
        snippet = dict(self.daemon.snippets.get(trigger, {}))
        snippet["enabled"] = not snippet.get("enabled", True)
        self.daemon.add_or_update_snippet(trigger, snippet)
        self.refresh_snippet_list()
        state = "Disabled" if not snippet["enabled"] else "Enabled"
        print(f"UI: {state} snippet '{trigger}' (saved)")

    def delete_snippet(self):
        trigger = self.selected_trigger()
        if not trigger or not self.daemon:
            return
        reply = QMessageBox.question(self, "Confirm", f"Delete '{trigger}'?")
        if reply == QMessageBox.Yes:
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
