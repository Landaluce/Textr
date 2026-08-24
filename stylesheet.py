dark_stylesheet = """
    QMainWindow, QDialog {
        background-color: #1e1e2e;
        color: #cdd6f4;
    }
    QWidget {
        background-color: #1e1e2e;
        color: #cdd6f4;
        font-family: 'Segoe UI', 'Ubuntu', 'Noto Sans', Arial, sans-serif;
        font-size: 14px;
    }
    QGroupBox {
        border: 1px solid #45475a;
        border-radius: 8px;
        margin-top: 15px;
        padding-top: 15px;
        font-weight: bold;
        color: #cba6f7;
    }
    QGroupBox::title {
        subcontrol-origin: margin;
        subcontrol-position: top left;
        left: 10px;
        padding: 0 5px;
    }
    QLineEdit, QTextEdit, QComboBox, QTreeWidget {
        background-color: #313244;
        color: #cdd6f4;
        border: 1px solid #45475a;
        border-radius: 6px;
        padding: 5px;
        selection-background-color: #b4befe;
        selection-color: #11111b;
    }
    QLineEdit:focus, QTextEdit:focus, QComboBox:focus, QTreeWidget:focus {
        border: 1px solid #b4befe;
    }
    QPushButton {
        background-color: #45475a;
        color: #cdd6f4;
        border: none;
        border-radius: 6px;
        padding: 8px 12px;
        font-weight: bold;
    }
    QPushButton:hover {
        background-color: #585b70;
        border: 1px solid #b4befe;
    }
    QPushButton:pressed {
        background-color: #313244;
    }
    /* State-colored toggles (green when active, red when inactive):
       Daemon button and Human Typing button */
    QPushButton#toggle_daemon_btn:!checked,
    QPushButton#toggle_writing_btn:!checked {
        background-color: #f38ba8;
        color: #11111b;
    }
    QPushButton#toggle_daemon_btn:checked,
    QPushButton#toggle_writing_btn:checked {
        background-color: #a6e3a1;
        color: #11111b;
    }
    QTreeWidget::item {
        padding: 6px;
        border-bottom: 1px solid #313244;
    }
    QTreeWidget::item:selected {
        background-color: #b4befe;
        color: #11111b;
        border-radius: 4px;
    }
    QTreeWidget::item:hover {
        background-color: #45475a;
        border-radius: 4px;
    }
    QLabel {
        color: #bac2de;
    }
    """