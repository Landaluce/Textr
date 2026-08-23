dark_stylesheet = """
    QMainWindow, QDialog {
        background-color: #1e1e2e;
        color: #cdd6f4;
    }
    QWidget {
        background-color: #1e1e2e;
        color: #cdd6f4;
        font-family: 'Segoe UI', Arial, sans-serif;
        font-size: 13px;
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
    QLineEdit, QTextEdit, QComboBox, QListWidget {
        background-color: #313244;
        color: #cdd6f4;
        border: 1px solid #45475a;
        border-radius: 6px;
        padding: 5px;
        selection-background-color: #b4befe;
        selection-color: #11111b;
    }
    QLineEdit:focus, QTextEdit:focus, QComboBox:focus, QListWidget:focus {
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
    /* State-colored toggles: Daemon = green when enabled / red when paused,
       Writing Mode = mauve accent while active */
    QPushButton#toggle_daemon_btn:!checked {
        background-color: #f38ba8;
        color: #11111b;
    }
    QPushButton#toggle_daemon_btn:checked {
        background-color: #a6e3a1;
        color: #11111b;
    }
    QPushButton#toggle_writing_btn:checked {
        background-color: #cba6f7;
        color: #11111b;
    }
    QListWidget::item {
        padding: 8px;
        border-bottom: 1px solid #313244;
    QListWidget::item:selected {
        background-color: #b4befe;
        color: #11111b;
        border-radius: 4px;
    }
    QListWidget::item:hover {
        background-color: #45475a;
        border-radius: 4px;
    }
    QLabel {
        color: #bac2de;
    }
    """