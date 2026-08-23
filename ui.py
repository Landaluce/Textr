# texter_mvp/ui.py
import sys
import json
import threading
from datetime import datetime
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QTextEdit, QPushButton, QComboBox,
    QListWidget, QListWidgetItem, QDialog, QFormLayout,
    QMessageBox, QFileDialog, QGroupBox, QSystemTrayIcon, QMenu
)
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QFont, QIcon, QAction, QPixmap, QPainter, QColor


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
        self.languages_input.setPlaceholderText('For template: JSON of languages\n{"python": "def f(): pass", "javascript": "function f() {}"}')
        self.languages_input.setFixedHeight(80)
        layout.addRow("Languages (JSON):", self.languages_input)

        # Action
        self.action_input = QLineEdit()
        self.action_input.setPlaceholderText("For command: set_language, toggle_pause, shutdown")
        layout.addRow("Action:", self.action_input)

        # Populate fields if editing
        if self.snippet:
            self.trigger_input.setText(self.snippet.get("trigger", ""))
            self.category_input.setCurrentText(self.snippet.get("category", "uncategorized"))
            self.type_combo.setCurrentText(self.snippet.get("type", "variable"))
            self.on_type_changed(self.snippet.get("type", "variable"))

            if self.snippet.get("type") == "variable":
                self.value_input.setText(self.snippet.get("value", ""))
            elif self.snippet.get("type") == "template":
                langs = self.snippet.get("languages", {})
                self.languages_input.setText(json.dumps(langs, indent=2))
            elif self.snippet.get("type") == "command":
                self.action_input.setText(self.snippet.get("action", ""))

        # Buttons
        btn_layout = QHBoxLayout()
        self.save_btn = QPushButton("Save")
        self.save_btn.clicked.connect(self.accept)
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(self.save_btn)
        btn_layout.addWidget(self.cancel_btn)
        layout.addRow(btn_layout)
        

    def on_type_changed(self, type_val):
        self.languages_input.setHidden(type_val != "template")
        self.action_input.setHidden(type_val != "command")

    def get_snippet(self):
        trigger = self.trigger_input.text().strip()
        snippet_type = self.type_combo.currentText()
        category = self.category_input.currentText().strip() or "uncategorized"

        snippet = {"trigger": trigger, "type": snippet_type, "category": category}

        if snippet_type == "variable":
            snippet["value"] = self.value_input.toPlainText().strip()
        elif snippet_type == "template":
            try:
                langs = json.loads(self.languages_input.toPlainText())
                snippet["languages"] = langs
            except json.JSONDecodeError:
                QMessageBox.critical(self, "Error", "Invalid JSON for languages")
                return None
        elif snippet_type == "command":
            snippet["action"] = self.action_input.text().strip()

        return snippet


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
        self.quit_requested.connect(self.quit_app)
        self.daemon_command.connect(self.handle_daemon_command)
        self.setup_ui()

    def set_app(self, app):
        """Set the QApplication reference"""
        self.app = app

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
        elif action == "insert_date":
            self.insert_text(datetime.now().strftime("%Y-%m-%d"))
        elif action == "insert_time":
            self.insert_text(datetime.now().strftime("%H:%M"))
        elif action == "insert_datetime":
            self.insert_text(datetime.now().strftime("%Y-%m-%d %H:%M"))
        elif action == "restart":
            self.restart_app()
        elif action == "sync_writing_mode":
            if not self.daemon:
                return
            self.toggle_writing_btn.blockSignals(True)
            self.toggle_writing_btn.setChecked(self.daemon.writing_mode)
            self.status_label.setText(
                "Status: Daemon: " + ("enabled" if self.daemon.enabled else "paused") +
                ", Writing mode: " + ("ON" if self.daemon.writing_mode else "OFF")
            )
            self.toggle_writing_btn.blockSignals(False)

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

    def save_user_config(self):
        if self.daemon:
            self.daemon.config["name"] = self.name_edit.text().strip()
            self.daemon.config["email"] = self.email_edit.text().strip()
            self.daemon.save_config()
            self.status_label.setText("Status: Settings saved!")

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
            self.status_label.setText(f"Status: Language switched to {self.daemon.current_language}")

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

        # Snippet list
        self.snippet_list = QListWidget()
        self.snippet_list.setFont(QFont("Arial", 16))  # family, size

        self.snippet_list.setSortingEnabled(False)  # We'll handle sorting manually
        from PySide6.QtWidgets import QAbstractItemView
        self.snippet_list.setDragDropMode(QAbstractItemView.InternalMove)
        self.snippet_list.itemSelectionChanged.connect(self.on_selection_changed)
        left_layout.addWidget(self.snippet_list)

        # Add/Edit/Delete buttons
        btn_layout = QHBoxLayout()
        self.add_btn = QPushButton("Add")
        self.add_btn.clicked.connect(self.add_snippet)
        self.edit_btn = QPushButton("Edit")
        self.edit_btn.clicked.connect(self.edit_snippet)
        self.delete_btn = QPushButton("Delete")
        self.delete_btn.clicked.connect(self.delete_snippet)
        btn_layout.addWidget(self.add_btn)
        btn_layout.addWidget(self.edit_btn)
        btn_layout.addWidget(self.delete_btn)
        left_layout.addLayout(btn_layout)

        main_layout.addWidget(left_group, 1)

        # Right panel: Details + Language selector
        right_group = QGroupBox("Settings")
        right_layout = QVBoxLayout(right_group)

        # Language selector
        lang_layout = QHBoxLayout()
        lang_layout.addWidget(QLabel("Current Language:"))
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
        self.save_settings_btn = QPushButton("Save Settings")
        self.save_settings_btn.clicked.connect(self.save_user_config)
        settings_layout.addRow("Name:", self.name_edit)
        settings_layout.addRow("Email:", self.email_edit)
        settings_layout.addRow(self.save_settings_btn)
        right_layout.addWidget(settings_group)

        # Status
        self.status_label = QLabel("Status: Ready, Daemon: enabled, Writing Mode: OFF")
        right_layout.addWidget(self.status_label)

        # Buttons
        action_layout = QHBoxLayout()

        self.toggle_daemon_btn = QPushButton("Daemon")
        self.toggle_daemon_btn.setCheckable(True)
        self.toggle_daemon_btn.setChecked(self.daemon.enabled if self.daemon else True)
        self.toggle_daemon_btn.toggled.connect(self.toggle_daemon)

        self.toggle_writing_btn = QPushButton("Writing Mode")
        self.toggle_writing_btn.setCheckable(True)
        self.toggle_writing_btn.setChecked(self.daemon.writing_mode if self.daemon else False)
        self.toggle_writing_btn.toggled.connect(self.toggle_writing_mode)

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
        self.snippet_list.setSortingEnabled(False)
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

        type_order = {"command": 0, "template": 1, "variable": 2}
        sorted_items = sorted(
            snippets.items(),
            key=lambda kv: (
                type_order.get(kv[1].get("type", "variable"), 99),
                kv[0].lower()
            )
        )

        for trigger, snippet in sorted_items:
            item = QListWidgetItem(trigger)
            item.setData(Qt.UserRole, trigger)

            snippet_type = snippet.get("type", "variable")
            if snippet_type == "command":
                item.setForeground(Qt.darkRed)
                item.setToolTip(f"Command | Category: {snippet.get('category', 'uncategorized')}")
            elif snippet_type == "template":
                item.setForeground(Qt.darkBlue)
                item.setToolTip(f"Template | Category: {snippet.get('category', 'uncategorized')}")
            else:
                item.setForeground(Qt.darkGreen)
                item.setToolTip(f"Variable | Category: {snippet.get('category', 'uncategorized')}")

            self.snippet_list.addItem(item)

    def on_category_changed(self, category):
        """Handle category dropdown change"""
        if self.daemon:
            self.daemon.set_category(category)
        self.refresh_snippet_list()
        self.status_label.setText(f"Status: Showing {category} snippets")

    def on_selection_changed(self):
        selected = self.snippet_list.selectedItems()
        if not selected or not self.daemon:
            self.preview_text.clear()
            return

        trigger = selected[0].data(Qt.UserRole)
        snippet = self.daemon.snippets.get(trigger)

        if snippet and snippet.get("type") == "template":
            langs = snippet.get("languages", {})
            current_lang = self.language_combo.currentText()
            preview = langs.get(current_lang, f"No preview for {current_lang}")
            self.preview_text.setText(preview)
        else:
            self.preview_text.clear()
        
        if snippet:
            preview = self.get_expansion_preview(snippet)
            snippet_category = snippet.get("category", "uncategorized")
            snippet_type = snippet.get("type", "variable")
            
            self.preview_text.setText(f"Category: {snippet_category}\nType: {snippet_type}\nTrigger: {trigger}\n\nExpands to:\n{preview}")

    def on_language_changed(self, lang):
        self.refresh_snippet_list()
        self.on_selection_changed()
        if self.daemon:
            self.daemon.current_language = lang
            self.status_label.setText(f"Status: Language switched to {lang}")
            print(f"UI: Language switched to {lang}")

    def bring_to_front(self):
        self.setWindowFlag(Qt.WindowStaysOnTopHint, True)
        self.show()
        self.raise_()
        self.activateWindow()
        QTimer.singleShot(200, self._clear_always_on_top)

    def _clear_always_on_top(self):
        self.setWindowFlag(Qt.WindowStaysOnTopHint, False)
        self.show()

    def hide_window(self):
        self.hide()

    def insert_text(self, text):
        self.preview_text.insertPlainText(text)

    def bring_to_front(self):
        self.show()
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def restart_app(self):
        self.quit_app()

    def add_snippet(self):
        categories = self.daemon.get_categories() if self.daemon else []
        dialog = AddSnippetDialog(self, categories=categories)
        if dialog.exec() == QDialog.Accepted:
            snippet = dialog.get_snippet()
            if snippet and self.daemon:
                trigger = snippet.pop("trigger")
                self.daemon.add_or_update_snippet(trigger, snippet)
                self.refresh_snippet_list()
                self.status_label.setText(f"Status: Added snippet '{trigger}' (saved)")

    def edit_snippet(self):
        selected = self.snippet_list.selectedItems()
        if not selected or not self.daemon:
            return

        trigger = selected[0].data(Qt.UserRole)
        snippet = {"trigger": trigger, **self.daemon.snippets.get(trigger, {})}

        categories = self.daemon.get_categories() if self.daemon else []
        dialog = AddSnippetDialog(self, snippet=snippet, categories=categories)
        if dialog.exec() == QDialog.Accepted:
            new_snippet = dialog.get_snippet()
            if new_snippet and self.daemon:
                new_trigger = new_snippet.pop("trigger")
                self.daemon.replace_snippet(trigger, new_trigger, new_snippet)
                self.refresh_snippet_list()
                self.status_label.setText(f"Status: Edited snippet '{new_trigger}' (saved)")

    def delete_snippet(self):
        selected = self.snippet_list.selectedItems()
        if not selected or not self.daemon:
            return

        trigger = selected[0].data(Qt.UserRole)
        reply = QMessageBox.question(self, "Confirm", f"Delete '{trigger}'?")
        if reply == QMessageBox.Yes:
            self.daemon.delete_snippet(trigger)
            self.refresh_snippet_list()
            self.status_label.setText(f"Status: Deleted snippet '{trigger}' (saved)")

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

        self.tray_icon.setIcon(create_status_icon("green" if enabled else "red"))
        self.tray_icon.setToolTip(f"Texter - {status.capitalize()}")

        writing = self.daemon.writing_mode if self.daemon else False
        self.status_label.setText(f"Status: Daemon: {status}, Writing Mode: {writing}")
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
        if self.daemon:
            self.daemon.writing_mode = checked
            self.status_label.setText(
                f"Status: Daemon: {'enabled' if self.daemon.enabled else 'paused'}, "
                f"Writing mode: {'ON' if checked else 'OFF'}"
            )
            print(f"UI: Writing mode {'enabled' if checked else 'disabled'}")

    def quit_button(self):
        """Button handler for manual quit"""
        if self.daemon:
            self.daemon.stop()
        self.quit_app()

    def import_json(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "Import Snippets", "", "JSON Files (*.json)")
        if file_path:
            try:
                with open(file_path, "r") as f:
                    snippets = json.load(f)
                if self.daemon:
                    self.daemon.import_snippets(snippets)
                    self.refresh_snippet_list()
                    self.status_label.setText(f"Status: Imported snippets from {file_path}")
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Failed to import: {e}")

    def export_json(self):
        file_path, _ = QFileDialog.getSaveFileName(self, "Export Snippets", "", "JSON Files (*.json)")
        if file_path:
            try:
                with open(file_path, "w") as f:
                    json.dump(self.daemon.snippets, f, indent=2)
                self.status_label.setText(f"Status: Exported snippets to {file_path}")
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Failed to export: {e}")
