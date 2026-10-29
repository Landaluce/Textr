dark_stylesheet = """
    QMainWindow, QDialog {
        background-color: #0b0b0e;
        color: #d6d6de;
    }
    QWidget {
        background-color: #0b0b0e;
        color: #d6d6de;
        font-family: 'Segoe UI', 'Ubuntu', 'Noto Sans', Arial, sans-serif;
        font-size: 14px;
    }
    QGroupBox {
        border: 1px solid #2e2e38;
        border-radius: 8px;
        margin-top: 15px;
        padding-top: 15px;
        font-weight: bold;
        color: #ff9a3c;
    }
    QGroupBox::title {
        subcontrol-origin: margin;
        subcontrol-position: top left;
        left: 10px;
        padding: 0 5px;
    }
    QLineEdit, QTextEdit, QComboBox {
        background-color: #15151c;
        color: #d6d6de;
        border: 1px solid #2e2e38;
        border-radius: 6px;
        padding: 5px;
        selection-background-color: #ff9a3c;
        selection-color: #11111b;
    }
    /* No selection-background-color here: it would make the view paint the
       row highlight across the full width, gutter included. Selection and
       hover panels are drawn by SnippetTreeDelegate (ui.py), clipped to the
       item's own cell. */
    QTreeWidget {
        background-color: #15151c;
        color: #d6d6de;
        border: 1px solid #2e2e38;
        border-radius: 6px;
        padding: 5px;
    }
    QLineEdit:focus, QTextEdit:focus, QComboBox:focus, QTreeWidget:focus {
        border: 1px solid #ff9a3c;
    }
    QPushButton {
        background-color: #15151c;
        color: #d6d6de;
        border: 1px solid #2e2e38;
        border-radius: 6px;
        padding: 8px 12px;
        font-weight: bold;
    }
    QPushButton:hover {
        background-color: #2e2e38;
        border: 1px solid #ff9a3c;
    }
    QPushButton:pressed {
        background-color: #0b0b0e;
    }
    /* State-colored toggles (orange when active, muted when inactive):
       Daemon button and Human Typing button */
    QPushButton#toggle_daemon_btn:!checked,
    QPushButton#toggle_writing_btn:!checked {
        background-color: #15151c;
        color: #9a9aa5;
    }
    QPushButton#toggle_daemon_btn:checked,
    QPushButton#toggle_writing_btn:checked {
        background-color: #ff9a3c;
        color: #11111b;
    }
    /* No QTreeWidget::item rules on purpose: the stylesheet engine stretches
       ::item backgrounds across the whole row, covering the indentation
       gutter. Selection/hover panels are painted by SnippetTreeDelegate
       (ui.py), clipped to the item's own cell. */
    QLabel {
        color: #9a9aa5;
    }
    """