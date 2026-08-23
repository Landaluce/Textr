# texter_mvp/ui.py
import sys
import json
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QTextEdit, QPushButton, QComboBox,
    QListWidget, QListWidgetItem, QDialog, QFormLayout,
    QMessageBox, QFileDialog, QGroupBox
)
from PySide6.QtCore import Qt


class AddSnippetDialog(QDialog):
    def __init__(self, parent=None, snippet=None):
        super().__init__(parent)
        self.snippet = snippet
        self.setup_ui()

    def setup_ui(self):
        self.setWindowTitle("Add/Edit Snippet")
        self.setMinimumWidth(400)

        layout = QFormLayout(self)

        self.trigger_input = QLineEdit()
        self.trigger_input.setPlaceholderText("e.g., mn, func, date")
        layout.addRow("Trigger:", self.trigger_input)

        self.type_combo = QComboBox()
        self.type_combo.addItems(["variable", "template", "command"])
        self.type_combo.currentTextChanged.connect(self.on_type_changed)
        layout.addRow("Type:", self.type_combo)

        self.value_input = QTextEdit()
        self.value_input.setPlaceholderText("For variable: the value")
        layout.addRow("Value:", self.value_input)

        self.languages_input = QTextEdit()
        self.languages_input.setPlaceholderText('For template: JSON of languages\n{"python": "def f(): pass", "javascript": "function f() {}"}')
        self.languages_input.setFixedHeight(80)
        layout.addRow("Languages (JSON):", self.languages_input)

        self.action_input = QLineEdit()
        self.action_input.setPlaceholderText("For command: set_language, toggle_pause, shutdown")
        layout.addRow("Action:", self.action_input)

        if self.snippet:
            self.trigger_input.setText(self.snippet.get("trigger", ""))
            self.type_combo.setCurrentText(self.snippet.get("type", "variable"))
            self.on_type_changed(self.snippet.get("type", "variable"))

            if self.snippet.get("type") == "variable":
                self.value_input.setText(self.snippet.get("value", ""))
            elif self.snippet.get("type") == "template":
                langs = self.snippet.get("languages", {})
                self.languages_input.setText(json.dumps(langs, indent=2))
            elif self.snippet.get("type") == "command":
                self.action_input.setText(self.snippet.get("action", ""))

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

        snippet = {"trigger": trigger, "type": snippet_type}

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
    def __init__(self, daemon=None):
        super().__init__()
        self.daemon = daemon
        self.setup_ui()

    def setup_ui(self):
        self.setWindowTitle("Texter MVP - Snippet Manager")
        self.setMinimumSize(800, 600)

        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QHBoxLayout(central)

        # Left panel: Snippet list
        left_group = QGroupBox("Snippets")
        left_layout = QVBoxLayout(left_group)

        self.snippet_list = QListWidget()
        self.snippet_list.itemSelectionChanged.connect(self.on_selection_changed)
        left_layout.addWidget(self.snippet_list)

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
        right_group = QGroupBox("Details & Settings")
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

        # Status
        self.status_label = QLabel("Status: Ready")
        right_layout.addWidget(self.status_label)

        # Buttons
        action_layout = QHBoxLayout()
        self.toggle_btn = QPushButton("Toggle Daemon")
        self.toggle_btn.clicked.connect(self.toggle_daemon)
        self.import_btn = QPushButton("Import JSON")
        self.import_btn.clicked.connect(self.import_json)
        self.export_btn = QPushButton("Export JSON")
        self.export_btn.clicked.connect(self.export_json)
        action_layout.addWidget(self.toggle_btn)
        action_layout.addWidget(self.import_btn)
        action_layout.addWidget(self.export_btn)
        right_layout.addLayout(action_layout)

        main_layout.addWidget(right_group, 2)

        self.refresh_snippet_list()

    def refresh_snippet_list(self):
        self.snippet_list.clear()
        if self.daemon:
            for trigger in self.daemon.snippets:
                item = QListWidgetItem(trigger)
                item.setData(Qt.UserRole, trigger)
                self.snippet_list.addItem(item)

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

    def on_language_changed(self, lang):
        self.refresh_snippet_list()
        self.on_selection_changed()
        if self.daemon:
            self.daemon.current_language = lang
            self.status_label.setText(f"Status: Language switched to {lang}")
            print(f"UI: Language switched to {lang}")

    def add_snippet(self):
        dialog = AddSnippetDialog(self)
        if dialog.exec() == QDialog.Accepted:
            snippet = dialog.get_snippet()
            if snippet and self.daemon:
                trigger = snippet.pop("trigger")
                self.daemon.snippets[trigger] = snippet
                self.refresh_snippet_list()
                self.status_label.setText(f"Status: Added snippet '{trigger}'")

    def edit_snippet(self):
        selected = self.snippet_list.selectedItems()
        if not selected or not self.daemon:
            return

        trigger = selected[0].data(Qt.UserRole)
        snippet = {"trigger": trigger, **self.daemon.snippets.get(trigger, {})}

        dialog = AddSnippetDialog(self, snippet=snippet)
        if dialog.exec() == QDialog.Accepted:
            new_snippet = dialog.get_snippet()
            if new_snippet and self.daemon:
                old_trigger = trigger
                new_trigger = new_snippet.pop("trigger")
                del self.daemon.snippets[old_trigger]
                self.daemon.snippets[new_trigger] = new_snippet
                self.refresh_snippet_list()
                self.status_label.setText(f"Status: Edited snippet '{new_trigger}'")

    def delete_snippet(self):
        selected = self.snippet_list.selectedItems()
        if not selected or not self.daemon:
            return

        trigger = selected[0].data(Qt.UserRole)
        reply = QMessageBox.question(self, "Confirm", f"Delete '{trigger}'?")
        if reply == QMessageBox.Yes:
            del self.daemon.snippets[trigger]
            self.refresh_snippet_list()
            self.status_label.setText(f"Status: Deleted snippet '{trigger}'")

    def toggle_daemon(self):
        if self.daemon:
            self.daemon.enabled = not self.daemon.enabled
            status = "enabled" if self.daemon.enabled else "paused"
            self.status_label.setText(f"Status: Daemon {status}")

    def import_json(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "Import Snippets", "", "JSON Files (*.json)")
        if file_path:
            try:
                with open(file_path, "r") as f:
                    snippets = json.load(f)
                if self.daemon:
                    self.daemon.snippets.update(snippets)
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


if __name__ == "__main__":
    from daemon import Daemon

    app = QApplication(sys.argv)
    daemon = Daemon()
    window = TexterUI(daemon=daemon)
    window.show()

    import threading
    daemon_thread = threading.Thread(target=daemon.run, daemon=True)
    daemon_thread.start()

    sys.exit(app.exec())