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
    QLineEdit, QTextEdit, QTextBrowser, QComboBox {
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
    /* Custom settings table only: keeps the old Catppuccin colors,
       exempt from the briefing theme used elsewhere. */
    QTableWidget {
        background-color: #313244;
        color: #cdd6f4;
        border: 1px solid #45475a;
        border-radius: 6px;
        gridline-color: #45475a;
        selection-background-color: #b4befe;
        selection-color: #11111b;
    }
    QHeaderView::section {
        background-color: #45475a;
        color: #cdd6f4;
        border: 1px solid #45475a;
        padding: 5px;
    }
    /* Snippet table only: briefing theme (the custom settings table above
       keeps the old colors). The ID selector outranks the generic rule. */
    QTableWidget#snippet_table {
        background-color: #15151c;
        color: #d6d6de;
        border: 1px solid #2e2e38;
        border-radius: 6px;
        gridline-color: #2e2e38;
        selection-background-color: #ff9a3c;
        selection-color: #11111b;
    }
    QTableWidget#snippet_table QHeaderView::section {
        background-color: #2e2e38;
        color: #d6d6de;
        border: 1px solid #2e2e38;
        padding: 5px;
    }
    QLineEdit:focus, QTextEdit:focus, QTextBrowser:focus, QComboBox:focus, QTreeWidget:focus {
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
    /* Quick-filter chips: small, checkable; the active chip is orange. */
    QPushButton#filter_chip {
        padding: 4px 10px;
        font-weight: normal;
    }
    QPushButton#filter_chip:checked {
        background-color: #ff9a3c;
        color: #11111b;
        border: 1px solid #ff9a3c;
        font-weight: bold;
    }
    /* Save Settings when edited but unsaved: orange outline + text. */
    QPushButton#save_settings_dirty {
        border: 1px solid #ff9a3c;
        color: #ff9a3c;
    }
    /* Right-panel scroll area: no frame, same dark background. */
    QScrollArea {
        background-color: #0b0b0e;
        border: none;
    }
    /* Primary action (Quick Insert): the one orange button, so it reads
       as the main action rather than one of many equal buttons. */
    QPushButton#primary_btn {
        background-color: #ff9a3c;
        color: #11111b;
        border: 1px solid #ff9a3c;
    }
    QPushButton#primary_btn:hover {
        background-color: #ffa64d;
        border: 1px solid #ffa64d;
    }
    QPushButton#primary_btn:pressed {
        background-color: #e07f1f;
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

light_stylesheet = """
    QMainWindow, QDialog {
        background-color: #f4f4f6;
        color: #1c1c22;
    }
    QWidget {
        background-color: #f4f4f6;
        color: #1c1c22;
        font-family: 'Segoe UI', 'Ubuntu', 'Noto Sans', Arial, sans-serif;
        font-size: 14px;
    }
    QGroupBox {
        border: 1px solid #d9d9de;
        border-radius: 8px;
        margin-top: 15px;
        padding-top: 15px;
        font-weight: bold;
        color: #c2410c;
    }
    QGroupBox::title {
        subcontrol-origin: margin;
        subcontrol-position: top left;
        left: 10px;
        padding: 0 5px;
    }
    QLineEdit, QTextEdit, QTextBrowser, QComboBox {
        background-color: #ffffff;
        color: #1c1c22;
        border: 1px solid #d9d9de;
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
        background-color: #ffffff;
        color: #1c1c22;
        border: 1px solid #d9d9de;
        border-radius: 6px;
        padding: 5px;
    }
    /* Custom settings table only: Catppuccin Latte, the light equivalent
       of the Mocha colors the dark theme keeps here. */
    QTableWidget {
        background-color: #eff1f5;
        color: #4c4f69;
        border: 1px solid #ccd0da;
        border-radius: 6px;
        gridline-color: #ccd0da;
        selection-background-color: #7287fd;
        selection-color: #eff1f5;
    }
    QHeaderView::section {
        background-color: #ccd0da;
        color: #4c4f69;
        border: 1px solid #ccd0da;
        padding: 5px;
    }
    /* Snippet table only: briefing theme (the custom settings table above
       keeps the Latte colors). The ID selector outranks the generic rule. */
    QTableWidget#snippet_table {
        background-color: #ffffff;
        color: #1c1c22;
        border: 1px solid #d9d9de;
        border-radius: 6px;
        gridline-color: #d9d9de;
        selection-background-color: #ff9a3c;
        selection-color: #11111b;
    }
    QTableWidget#snippet_table QHeaderView::section {
        background-color: #ececf0;
        color: #1c1c22;
        border: 1px solid #d9d9de;
        padding: 5px;
    }
    QLineEdit:focus, QTextEdit:focus, QTextBrowser:focus, QComboBox:focus, QTreeWidget:focus {
        border: 1px solid #ff9a3c;
    }
    QPushButton {
        background-color: #ffffff;
        color: #1c1c22;
        border: 1px solid #d9d9de;
        border-radius: 6px;
        padding: 8px 12px;
        font-weight: bold;
    }
    QPushButton:hover {
        background-color: #ececf0;
        border: 1px solid #ff9a3c;
    }
    /* Quick-filter chips: small, checkable; the active chip is orange. */
    QPushButton#filter_chip {
        padding: 4px 10px;
        font-weight: normal;
    }
    QPushButton#filter_chip:checked {
        background-color: #ff9a3c;
        color: #11111b;
        border: 1px solid #ff9a3c;
        font-weight: bold;
    }
    /* Save Settings when edited but unsaved: orange outline + text. */
    QPushButton#save_settings_dirty {
        border: 1px solid #ff9a3c;
        color: #c2410c;
    }
    /* Right-panel scroll area: no frame, same light background. */
    QScrollArea {
        background-color: #f4f4f6;
        border: none;
    }
    /* Primary action (Quick Insert): the one orange button, so it reads
       as the main action rather than one of many equal buttons. */
    QPushButton#primary_btn {
        background-color: #ff9a3c;
        color: #11111b;
        border: 1px solid #ff9a3c;
    }
    QPushButton#primary_btn:hover {
        background-color: #ffa64d;
        border: 1px solid #ffa64d;
    }
    QPushButton#primary_btn:pressed {
        background-color: #e07f1f;
    }
    QPushButton:pressed {
        background-color: #ececf0;
    }
    /* State-colored toggles (orange when active, muted when inactive):
       Daemon button and Human Typing button */
    QPushButton#toggle_daemon_btn:!checked,
    QPushButton#toggle_writing_btn:!checked {
        background-color: #ffffff;
        color: #6a6a75;
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
        color: #6a6a75;
    }
    """
