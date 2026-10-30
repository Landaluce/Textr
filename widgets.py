# texter_mvp/widgets.py
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
        # Move to category (item 3): submenu of existing categories, the
        # current one ticked/disabled for a single selection, plus a
        # "New category..." prompt. Applies to all selected rows, like
        # the bulk action, through one shared window method.
        move_menu = QMenu("Move to category", self)
        categories = (
            window.daemon.get_categories()
            if window.daemon and hasattr(window.daemon, "get_categories")
            else []
        )
        selected = window.selected_triggers() if hasattr(window, "selected_triggers") else [trigger]
        current_cats = {
            (window.daemon.snippets.get(t, {}) or {}).get("category", "uncategorized")
            for t in selected
        }
        for cat in categories:
            act = move_menu.addAction(cat)
            act.setCheckable(True)
            act.setChecked(len(current_cats) == 1 and cat in current_cats)
            act.setEnabled(not (len(selected) == 1 and cat in current_cats))
            act.triggered.connect(lambda _c, c=cat: window.move_selected_to_category(c))
        move_menu.addSeparator()
        move_menu.addAction("New category\u2026", window.prompt_move_to_new_category)
        menu.addMenu(move_menu)
        menu.addSeparator()
        menu.addAction("Delete", window.delete_snippet)
        menu.exec(event.globalPos())
