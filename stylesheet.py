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


mocha_stylesheet = """
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
        color: #b4befe;
    }
    QGroupBox::title {
        subcontrol-origin: margin;
        subcontrol-position: top left;
        left: 10px;
        padding: 0 5px;
    }
    QLineEdit, QTextEdit, QTextBrowser, QComboBox {
        background-color: #313244;
        color: #cdd6f4;
        border: 1px solid #45475a;
        border-radius: 6px;
        padding: 5px;
        selection-background-color: #b4befe;
        selection-color: #11111b;
    }
    /* No selection-background-color here: it would make the view paint the
       row highlight across the full width, gutter included. Selection and
       hover panels are drawn by SnippetTreeDelegate (ui.py), clipped to the
       item's own cell. */
    QTreeWidget {
        background-color: #313244;
        color: #cdd6f4;
        border: 1px solid #45475a;
        border-radius: 6px;
        padding: 5px;
    }
    /* Custom settings table only: theme surface tones, kept distinct from
       the snippet table below. */
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
    /* Snippet table only: the ID selector outranks the generic rule. */
    QTableWidget#snippet_table {
        background-color: #313244;
        color: #cdd6f4;
        border: 1px solid #45475a;
        border-radius: 6px;
        gridline-color: #45475a;
        selection-background-color: #b4befe;
        selection-color: #11111b;
    }
    QTableWidget#snippet_table QHeaderView::section {
        background-color: #45475a;
        color: #cdd6f4;
        border: 1px solid #45475a;
        padding: 5px;
    }
    QLineEdit:focus, QTextEdit:focus, QTextBrowser:focus, QComboBox:focus, QTreeWidget:focus {
        border: 1px solid #b4befe;
    }
    QPushButton {
        background-color: #313244;
        color: #cdd6f4;
        border: 1px solid #45475a;
        border-radius: 6px;
        padding: 8px 12px;
        font-weight: bold;
    }
    QPushButton:hover {
        background-color: #45475a;
        border: 1px solid #b4befe;
    }
    /* Quick-filter chips: small, checkable; the active chip uses the accent. */
    QPushButton#filter_chip {
        padding: 4px 10px;
        font-weight: normal;
    }
    QPushButton#filter_chip:checked {
        background-color: #b4befe;
        color: #11111b;
        border: 1px solid #b4befe;
        font-weight: bold;
    }
    /* Save Settings when edited but unsaved: accent outline + text. */
    QPushButton#save_settings_dirty {
        border: 1px solid #b4befe;
        color: #b4befe;
    }
    /* Right-panel scroll area: no frame, same background. */
    QScrollArea {
        background-color: #1e1e2e;
        border: none;
    }
    /* Primary action (Quick Insert): the one accent button, so it reads
       as the main action rather than one of many equal buttons. */
    QPushButton#primary_btn {
        background-color: #b4befe;
        color: #11111b;
        border: 1px solid #b4befe;
    }
    QPushButton#primary_btn:hover {
        background-color: #c9d2fd;
        border: 1px solid #c9d2fd;
    }
    QPushButton#primary_btn:pressed {
        background-color: #9aa7e8;
    }
    QPushButton:pressed {
        background-color: #1e1e2e;
    }
    /* State-colored toggles (accent when active, muted when inactive):
       Daemon button and Human Typing button */
    QPushButton#toggle_daemon_btn:!checked,
    QPushButton#toggle_writing_btn:!checked {
        background-color: #313244;
        color: #9399b2;
    }
    QPushButton#toggle_daemon_btn:checked,
    QPushButton#toggle_writing_btn:checked {
        background-color: #b4befe;
        color: #11111b;
    }
    /* No QTreeWidget::item rules on purpose: the stylesheet engine stretches
       ::item backgrounds across the whole row, covering the indentation
       gutter. Selection/hover panels are painted by SnippetTreeDelegate
       (ui.py), clipped to the item's own cell. */
    QLabel {
        color: #9399b2;
    }
    """


nord_stylesheet = """
    QMainWindow, QDialog {
        background-color: #2e3440;
        color: #d8dee9;
    }
    QWidget {
        background-color: #2e3440;
        color: #d8dee9;
        font-family: 'Segoe UI', 'Ubuntu', 'Noto Sans', Arial, sans-serif;
        font-size: 14px;
    }
    QGroupBox {
        border: 1px solid #4c566a;
        border-radius: 8px;
        margin-top: 15px;
        padding-top: 15px;
        font-weight: bold;
        color: #88c0d0;
    }
    QGroupBox::title {
        subcontrol-origin: margin;
        subcontrol-position: top left;
        left: 10px;
        padding: 0 5px;
    }
    QLineEdit, QTextEdit, QTextBrowser, QComboBox {
        background-color: #3b4252;
        color: #d8dee9;
        border: 1px solid #4c566a;
        border-radius: 6px;
        padding: 5px;
        selection-background-color: #88c0d0;
        selection-color: #2e3440;
    }
    /* No selection-background-color here: it would make the view paint the
       row highlight across the full width, gutter included. Selection and
       hover panels are drawn by SnippetTreeDelegate (ui.py), clipped to the
       item's own cell. */
    QTreeWidget {
        background-color: #3b4252;
        color: #d8dee9;
        border: 1px solid #4c566a;
        border-radius: 6px;
        padding: 5px;
    }
    /* Custom settings table only: theme surface tones, kept distinct from
       the snippet table below. */
    QTableWidget {
        background-color: #3b4252;
        color: #d8dee9;
        border: 1px solid #4c566a;
        border-radius: 6px;
        gridline-color: #4c566a;
        selection-background-color: #88c0d0;
        selection-color: #2e3440;
    }
    QHeaderView::section {
        background-color: #4c566a;
        color: #d8dee9;
        border: 1px solid #4c566a;
        padding: 5px;
    }
    /* Snippet table only: the ID selector outranks the generic rule. */
    QTableWidget#snippet_table {
        background-color: #3b4252;
        color: #d8dee9;
        border: 1px solid #4c566a;
        border-radius: 6px;
        gridline-color: #4c566a;
        selection-background-color: #88c0d0;
        selection-color: #2e3440;
    }
    QTableWidget#snippet_table QHeaderView::section {
        background-color: #4c566a;
        color: #d8dee9;
        border: 1px solid #4c566a;
        padding: 5px;
    }
    QLineEdit:focus, QTextEdit:focus, QTextBrowser:focus, QComboBox:focus, QTreeWidget:focus {
        border: 1px solid #88c0d0;
    }
    QPushButton {
        background-color: #3b4252;
        color: #d8dee9;
        border: 1px solid #4c566a;
        border-radius: 6px;
        padding: 8px 12px;
        font-weight: bold;
    }
    QPushButton:hover {
        background-color: #4c566a;
        border: 1px solid #88c0d0;
    }
    /* Quick-filter chips: small, checkable; the active chip uses the accent. */
    QPushButton#filter_chip {
        padding: 4px 10px;
        font-weight: normal;
    }
    QPushButton#filter_chip:checked {
        background-color: #88c0d0;
        color: #2e3440;
        border: 1px solid #88c0d0;
        font-weight: bold;
    }
    /* Save Settings when edited but unsaved: accent outline + text. */
    QPushButton#save_settings_dirty {
        border: 1px solid #88c0d0;
        color: #88c0d0;
    }
    /* Right-panel scroll area: no frame, same background. */
    QScrollArea {
        background-color: #2e3440;
        border: none;
    }
    /* Primary action (Quick Insert): the one accent button, so it reads
       as the main action rather than one of many equal buttons. */
    QPushButton#primary_btn {
        background-color: #88c0d0;
        color: #2e3440;
        border: 1px solid #88c0d0;
    }
    QPushButton#primary_btn:hover {
        background-color: #9cc7d6;
        border: 1px solid #9cc7d6;
    }
    QPushButton#primary_btn:pressed {
        background-color: #6fa8bd;
    }
    QPushButton:pressed {
        background-color: #2e3440;
    }
    /* State-colored toggles (accent when active, muted when inactive):
       Daemon button and Human Typing button */
    QPushButton#toggle_daemon_btn:!checked,
    QPushButton#toggle_writing_btn:!checked {
        background-color: #3b4252;
        color: #7b8694;
    }
    QPushButton#toggle_daemon_btn:checked,
    QPushButton#toggle_writing_btn:checked {
        background-color: #88c0d0;
        color: #2e3440;
    }
    /* No QTreeWidget::item rules on purpose: the stylesheet engine stretches
       ::item backgrounds across the whole row, covering the indentation
       gutter. Selection/hover panels are painted by SnippetTreeDelegate
       (ui.py), clipped to the item's own cell. */
    QLabel {
        color: #7b8694;
    }
    """


dracula_stylesheet = """
    QMainWindow, QDialog {
        background-color: #282a36;
        color: #f8f8f2;
    }
    QWidget {
        background-color: #282a36;
        color: #f8f8f2;
        font-family: 'Segoe UI', 'Ubuntu', 'Noto Sans', Arial, sans-serif;
        font-size: 14px;
    }
    QGroupBox {
        border: 1px solid #6272a4;
        border-radius: 8px;
        margin-top: 15px;
        padding-top: 15px;
        font-weight: bold;
        color: #ff79c6;
    }
    QGroupBox::title {
        subcontrol-origin: margin;
        subcontrol-position: top left;
        left: 10px;
        padding: 0 5px;
    }
    QLineEdit, QTextEdit, QTextBrowser, QComboBox {
        background-color: #44475a;
        color: #f8f8f2;
        border: 1px solid #6272a4;
        border-radius: 6px;
        padding: 5px;
        selection-background-color: #ff79c6;
        selection-color: #282a36;
    }
    /* No selection-background-color here: it would make the view paint the
       row highlight across the full width, gutter included. Selection and
       hover panels are drawn by SnippetTreeDelegate (ui.py), clipped to the
       item's own cell. */
    QTreeWidget {
        background-color: #44475a;
        color: #f8f8f2;
        border: 1px solid #6272a4;
        border-radius: 6px;
        padding: 5px;
    }
    /* Custom settings table only: theme surface tones, kept distinct from
       the snippet table below. */
    QTableWidget {
        background-color: #44475a;
        color: #f8f8f2;
        border: 1px solid #6272a4;
        border-radius: 6px;
        gridline-color: #6272a4;
        selection-background-color: #ff79c6;
        selection-color: #282a36;
    }
    QHeaderView::section {
        background-color: #6272a4;
        color: #f8f8f2;
        border: 1px solid #6272a4;
        padding: 5px;
    }
    /* Snippet table only: the ID selector outranks the generic rule. */
    QTableWidget#snippet_table {
        background-color: #44475a;
        color: #f8f8f2;
        border: 1px solid #6272a4;
        border-radius: 6px;
        gridline-color: #6272a4;
        selection-background-color: #ff79c6;
        selection-color: #282a36;
    }
    QTableWidget#snippet_table QHeaderView::section {
        background-color: #6272a4;
        color: #f8f8f2;
        border: 1px solid #6272a4;
        padding: 5px;
    }
    QLineEdit:focus, QTextEdit:focus, QTextBrowser:focus, QComboBox:focus, QTreeWidget:focus {
        border: 1px solid #ff79c6;
    }
    QPushButton {
        background-color: #44475a;
        color: #f8f8f2;
        border: 1px solid #6272a4;
        border-radius: 6px;
        padding: 8px 12px;
        font-weight: bold;
    }
    QPushButton:hover {
        background-color: #6272a4;
        border: 1px solid #ff79c6;
    }
    /* Quick-filter chips: small, checkable; the active chip uses the accent. */
    QPushButton#filter_chip {
        padding: 4px 10px;
        font-weight: normal;
    }
    QPushButton#filter_chip:checked {
        background-color: #ff79c6;
        color: #282a36;
        border: 1px solid #ff79c6;
        font-weight: bold;
    }
    /* Save Settings when edited but unsaved: accent outline + text. */
    QPushButton#save_settings_dirty {
        border: 1px solid #ff79c6;
        color: #ff79c6;
    }
    /* Right-panel scroll area: no frame, same background. */
    QScrollArea {
        background-color: #282a36;
        border: none;
    }
    /* Primary action (Quick Insert): the one accent button, so it reads
       as the main action rather than one of many equal buttons. */
    QPushButton#primary_btn {
        background-color: #ff79c6;
        color: #282a36;
        border: 1px solid #ff79c6;
    }
    QPushButton#primary_btn:hover {
        background-color: #ff92d0;
        border: 1px solid #ff92d0;
    }
    QPushButton#primary_btn:pressed {
        background-color: #e85fae;
    }
    QPushButton:pressed {
        background-color: #282a36;
    }
    /* State-colored toggles (accent when active, muted when inactive):
       Daemon button and Human Typing button */
    QPushButton#toggle_daemon_btn:!checked,
    QPushButton#toggle_writing_btn:!checked {
        background-color: #44475a;
        color: #6272a4;
    }
    QPushButton#toggle_daemon_btn:checked,
    QPushButton#toggle_writing_btn:checked {
        background-color: #ff79c6;
        color: #282a36;
    }
    /* No QTreeWidget::item rules on purpose: the stylesheet engine stretches
       ::item backgrounds across the whole row, covering the indentation
       gutter. Selection/hover panels are painted by SnippetTreeDelegate
       (ui.py), clipped to the item's own cell. */
    QLabel {
        color: #6272a4;
    }
    """


gruvbox_stylesheet = """
    QMainWindow, QDialog {
        background-color: #282828;
        color: #ebdbb2;
    }
    QWidget {
        background-color: #282828;
        color: #ebdbb2;
        font-family: 'Segoe UI', 'Ubuntu', 'Noto Sans', Arial, sans-serif;
        font-size: 14px;
    }
    QGroupBox {
        border: 1px solid #504945;
        border-radius: 8px;
        margin-top: 15px;
        padding-top: 15px;
        font-weight: bold;
        color: #fe8019;
    }
    QGroupBox::title {
        subcontrol-origin: margin;
        subcontrol-position: top left;
        left: 10px;
        padding: 0 5px;
    }
    QLineEdit, QTextEdit, QTextBrowser, QComboBox {
        background-color: #3c3836;
        color: #ebdbb2;
        border: 1px solid #504945;
        border-radius: 6px;
        padding: 5px;
        selection-background-color: #fe8019;
        selection-color: #282828;
    }
    /* No selection-background-color here: it would make the view paint the
       row highlight across the full width, gutter included. Selection and
       hover panels are drawn by SnippetTreeDelegate (ui.py), clipped to the
       item's own cell. */
    QTreeWidget {
        background-color: #3c3836;
        color: #ebdbb2;
        border: 1px solid #504945;
        border-radius: 6px;
        padding: 5px;
    }
    /* Custom settings table only: theme surface tones, kept distinct from
       the snippet table below. */
    QTableWidget {
        background-color: #3c3836;
        color: #ebdbb2;
        border: 1px solid #504945;
        border-radius: 6px;
        gridline-color: #504945;
        selection-background-color: #fe8019;
        selection-color: #282828;
    }
    QHeaderView::section {
        background-color: #504945;
        color: #ebdbb2;
        border: 1px solid #504945;
        padding: 5px;
    }
    /* Snippet table only: the ID selector outranks the generic rule. */
    QTableWidget#snippet_table {
        background-color: #3c3836;
        color: #ebdbb2;
        border: 1px solid #504945;
        border-radius: 6px;
        gridline-color: #504945;
        selection-background-color: #fe8019;
        selection-color: #282828;
    }
    QTableWidget#snippet_table QHeaderView::section {
        background-color: #504945;
        color: #ebdbb2;
        border: 1px solid #504945;
        padding: 5px;
    }
    QLineEdit:focus, QTextEdit:focus, QTextBrowser:focus, QComboBox:focus, QTreeWidget:focus {
        border: 1px solid #fe8019;
    }
    QPushButton {
        background-color: #3c3836;
        color: #ebdbb2;
        border: 1px solid #504945;
        border-radius: 6px;
        padding: 8px 12px;
        font-weight: bold;
    }
    QPushButton:hover {
        background-color: #504945;
        border: 1px solid #fe8019;
    }
    /* Quick-filter chips: small, checkable; the active chip uses the accent. */
    QPushButton#filter_chip {
        padding: 4px 10px;
        font-weight: normal;
    }
    QPushButton#filter_chip:checked {
        background-color: #fe8019;
        color: #282828;
        border: 1px solid #fe8019;
        font-weight: bold;
    }
    /* Save Settings when edited but unsaved: accent outline + text. */
    QPushButton#save_settings_dirty {
        border: 1px solid #fe8019;
        color: #fe8019;
    }
    /* Right-panel scroll area: no frame, same background. */
    QScrollArea {
        background-color: #282828;
        border: none;
    }
    /* Primary action (Quick Insert): the one accent button, so it reads
       as the main action rather than one of many equal buttons. */
    QPushButton#primary_btn {
        background-color: #fe8019;
        color: #282828;
        border: 1px solid #fe8019;
    }
    QPushButton#primary_btn:hover {
        background-color: #ff9a3d;
        border: 1px solid #ff9a3d;
    }
    QPushButton#primary_btn:pressed {
        background-color: #e06a00;
    }
    QPushButton:pressed {
        background-color: #282828;
    }
    /* State-colored toggles (accent when active, muted when inactive):
       Daemon button and Human Typing button */
    QPushButton#toggle_daemon_btn:!checked,
    QPushButton#toggle_writing_btn:!checked {
        background-color: #3c3836;
        color: #928374;
    }
    QPushButton#toggle_daemon_btn:checked,
    QPushButton#toggle_writing_btn:checked {
        background-color: #fe8019;
        color: #282828;
    }
    /* No QTreeWidget::item rules on purpose: the stylesheet engine stretches
       ::item backgrounds across the whole row, covering the indentation
       gutter. Selection/hover panels are painted by SnippetTreeDelegate
       (ui.py), clipped to the item's own cell. */
    QLabel {
        color: #928374;
    }
    """


high_contrast_stylesheet = """
    QMainWindow, QDialog {
        background-color: #000000;
        color: #ffffff;
    }
    QWidget {
        background-color: #000000;
        color: #ffffff;
        font-family: 'Segoe UI', 'Ubuntu', 'Noto Sans', Arial, sans-serif;
        font-size: 14px;
    }
    QGroupBox {
        border: 1px solid #808080;
        border-radius: 8px;
        margin-top: 15px;
        padding-top: 15px;
        font-weight: bold;
        color: #ffd700;
    }
    QGroupBox::title {
        subcontrol-origin: margin;
        subcontrol-position: top left;
        left: 10px;
        padding: 0 5px;
    }
    QLineEdit, QTextEdit, QTextBrowser, QComboBox {
        background-color: #1a1a1a;
        color: #ffffff;
        border: 1px solid #808080;
        border-radius: 6px;
        padding: 5px;
        selection-background-color: #ffd700;
        selection-color: #000000;
    }
    /* No selection-background-color here: it would make the view paint the
       row highlight across the full width, gutter included. Selection and
       hover panels are drawn by SnippetTreeDelegate (ui.py), clipped to the
       item's own cell. */
    QTreeWidget {
        background-color: #1a1a1a;
        color: #ffffff;
        border: 1px solid #808080;
        border-radius: 6px;
        padding: 5px;
    }
    /* Custom settings table only: theme surface tones, kept distinct from
       the snippet table below. */
    QTableWidget {
        background-color: #1a1a1a;
        color: #ffffff;
        border: 1px solid #808080;
        border-radius: 6px;
        gridline-color: #808080;
        selection-background-color: #ffd700;
        selection-color: #000000;
    }
    QHeaderView::section {
        background-color: #808080;
        color: #ffffff;
        border: 1px solid #808080;
        padding: 5px;
    }
    /* Snippet table only: the ID selector outranks the generic rule. */
    QTableWidget#snippet_table {
        background-color: #1a1a1a;
        color: #ffffff;
        border: 1px solid #808080;
        border-radius: 6px;
        gridline-color: #808080;
        selection-background-color: #ffd700;
        selection-color: #000000;
    }
    QTableWidget#snippet_table QHeaderView::section {
        background-color: #333333;
        color: #ffffff;
        border: 1px solid #808080;
        padding: 5px;
    }
    QLineEdit:focus, QTextEdit:focus, QTextBrowser:focus, QComboBox:focus, QTreeWidget:focus {
        border: 1px solid #ffd700;
    }
    QPushButton {
        background-color: #1a1a1a;
        color: #ffffff;
        border: 1px solid #808080;
        border-radius: 6px;
        padding: 8px 12px;
        font-weight: bold;
    }
    QPushButton:hover {
        background-color: #333333;
        border: 1px solid #ffd700;
    }
    /* Quick-filter chips: small, checkable; the active chip uses the accent. */
    QPushButton#filter_chip {
        padding: 4px 10px;
        font-weight: normal;
    }
    QPushButton#filter_chip:checked {
        background-color: #ffd700;
        color: #000000;
        border: 1px solid #ffd700;
        font-weight: bold;
    }
    /* Save Settings when edited but unsaved: accent outline + text. */
    QPushButton#save_settings_dirty {
        border: 1px solid #ffd700;
        color: #ffd700;
    }
    /* Right-panel scroll area: no frame, same background. */
    QScrollArea {
        background-color: #000000;
        border: none;
    }
    /* Primary action (Quick Insert): the one accent button, so it reads
       as the main action rather than one of many equal buttons. */
    QPushButton#primary_btn {
        background-color: #ffd700;
        color: #000000;
        border: 1px solid #ffd700;
    }
    QPushButton#primary_btn:hover {
        background-color: #ffe14d;
        border: 1px solid #ffe14d;
    }
    QPushButton#primary_btn:pressed {
        background-color: #e6c200;
    }
    QPushButton:pressed {
        background-color: #000000;
    }
    /* State-colored toggles (accent when active, muted when inactive):
       Daemon button and Human Typing button */
    QPushButton#toggle_daemon_btn:!checked,
    QPushButton#toggle_writing_btn:!checked {
        background-color: #1a1a1a;
        color: #c0c0c0;
    }
    QPushButton#toggle_daemon_btn:checked,
    QPushButton#toggle_writing_btn:checked {
        background-color: #ffd700;
        color: #000000;
    }
    /* No QTreeWidget::item rules on purpose: the stylesheet engine stretches
       ::item backgrounds across the whole row, covering the indentation
       gutter. Selection/hover panels are painted by SnippetTreeDelegate
       (ui.py), clipped to the item's own cell. */
    QLabel {
        color: #c0c0c0;
    }
    """


sepia_stylesheet = """
    QMainWindow, QDialog {
        background-color: #f5ecd7;
        color: #4a4238;
    }
    QWidget {
        background-color: #f5ecd7;
        color: #4a4238;
        font-family: 'Segoe UI', 'Ubuntu', 'Noto Sans', Arial, sans-serif;
        font-size: 14px;
    }
    QGroupBox {
        border: 1px solid #d9cfae;
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
        background-color: #fffaf0;
        color: #4a4238;
        border: 1px solid #d9cfae;
        border-radius: 6px;
        padding: 5px;
        selection-background-color: #c2410c;
        selection-color: #fffaf0;
    }
    /* No selection-background-color here: it would make the view paint the
       row highlight across the full width, gutter included. Selection and
       hover panels are drawn by SnippetTreeDelegate (ui.py), clipped to the
       item's own cell. */
    QTreeWidget {
        background-color: #fffaf0;
        color: #4a4238;
        border: 1px solid #d9cfae;
        border-radius: 6px;
        padding: 5px;
    }
    /* Custom settings table only: theme surface tones, kept distinct from
       the snippet table below. */
    QTableWidget {
        background-color: #f0e6d2;
        color: #4a4238;
        border: 1px solid #d9cfae;
        border-radius: 6px;
        gridline-color: #d9cfae;
        selection-background-color: #c2410c;
        selection-color: #fffaf0;
    }
    QHeaderView::section {
        background-color: #d9cfae;
        color: #4a4238;
        border: 1px solid #d9cfae;
        padding: 5px;
    }
    /* Snippet table only: the ID selector outranks the generic rule. */
    QTableWidget#snippet_table {
        background-color: #fffaf0;
        color: #4a4238;
        border: 1px solid #d9cfae;
        border-radius: 6px;
        gridline-color: #d9cfae;
        selection-background-color: #c2410c;
        selection-color: #fffaf0;
    }
    QTableWidget#snippet_table QHeaderView::section {
        background-color: #efe4c8;
        color: #4a4238;
        border: 1px solid #d9cfae;
        padding: 5px;
    }
    QLineEdit:focus, QTextEdit:focus, QTextBrowser:focus, QComboBox:focus, QTreeWidget:focus {
        border: 1px solid #c2410c;
    }
    QPushButton {
        background-color: #fffaf0;
        color: #4a4238;
        border: 1px solid #d9cfae;
        border-radius: 6px;
        padding: 8px 12px;
        font-weight: bold;
    }
    QPushButton:hover {
        background-color: #efe4c8;
        border: 1px solid #c2410c;
    }
    /* Quick-filter chips: small, checkable; the active chip uses the accent. */
    QPushButton#filter_chip {
        padding: 4px 10px;
        font-weight: normal;
    }
    QPushButton#filter_chip:checked {
        background-color: #c2410c;
        color: #fffaf0;
        border: 1px solid #c2410c;
        font-weight: bold;
    }
    /* Save Settings when edited but unsaved: accent outline + text. */
    QPushButton#save_settings_dirty {
        border: 1px solid #c2410c;
        color: #c2410c;
    }
    /* Right-panel scroll area: no frame, same background. */
    QScrollArea {
        background-color: #f5ecd7;
        border: none;
    }
    /* Primary action (Quick Insert): the one accent button, so it reads
       as the main action rather than one of many equal buttons. */
    QPushButton#primary_btn {
        background-color: #c2410c;
        color: #fffaf0;
        border: 1px solid #c2410c;
    }
    QPushButton#primary_btn:hover {
        background-color: #d6541a;
        border: 1px solid #d6541a;
    }
    QPushButton#primary_btn:pressed {
        background-color: #a83409;
    }
    QPushButton:pressed {
        background-color: #efe4c8;
    }
    /* State-colored toggles (accent when active, muted when inactive):
       Daemon button and Human Typing button */
    QPushButton#toggle_daemon_btn:!checked,
    QPushButton#toggle_writing_btn:!checked {
        background-color: #fffaf0;
        color: #8a7f70;
    }
    QPushButton#toggle_daemon_btn:checked,
    QPushButton#toggle_writing_btn:checked {
        background-color: #c2410c;
        color: #fffaf0;
    }
    /* No QTreeWidget::item rules on purpose: the stylesheet engine stretches
       ::item backgrounds across the whole row, covering the indentation
       gutter. Selection/hover panels are painted by SnippetTreeDelegate
       (ui.py), clipped to the item's own cell. */
    QLabel {
        color: #8a7f70;
    }
    """


THEME_COLORS = {
    # Code-facing colors per theme: row text, preview placeholders, picker
    # headers, and the tree delegate's painted panels. Stylesheet-only
    # values (backgrounds, borders) live in the palettes above.
    "dark": dict(
        text="#d6d6de", muted="#9a9aa5", accent="#ff9a3c",
        accent_text="#11111b", tree_bg="#15151c", hover="#2e2e38",
        type_command="#f38ba8", type_template="#89b4fa",
        type_variable="#a6e3a1", disabled="#9a9aa5",
        preview_present="#ff9a3c", preview_missing="#f38ba8",
        picker_header="#ff9a3c",
    ),
    "light": dict(
        text="#1c1c22", muted="#6a6a75", accent="#ff9a3c",
        accent_text="#11111b", tree_bg="#ffffff", hover="#ececf0",
        type_command="#c62828", type_template="#1d4ed8",
        type_variable="#2e7d32", disabled="#6a6a75",
        preview_present="#c2410c", preview_missing="#be123c",
        picker_header="#c2410c",
    ),
    "mocha": dict(
        text="#cdd6f4", muted="#9399b2", accent="#b4befe",
        accent_text="#11111b", tree_bg="#313244", hover="#45475a",
        type_command="#f38ba8", type_template="#89b4fa",
        type_variable="#a6e3a1", disabled="#9399b2",
        preview_present="#fab387", preview_missing="#f38ba8",
        picker_header="#b4befe",
    ),
    "nord": dict(
        text="#d8dee9", muted="#7b8694", accent="#88c0d0",
        accent_text="#2e3440", tree_bg="#3b4252", hover="#4c566a",
        type_command="#bf616a", type_template="#81a1c1",
        type_variable="#a3be8c", disabled="#7b8694",
        preview_present="#88c0d0", preview_missing="#bf616a",
        picker_header="#88c0d0",
    ),
    "dracula": dict(
        text="#f8f8f2", muted="#6272a4", accent="#ff79c6",
        accent_text="#282a36", tree_bg="#44475a", hover="#6272a4",
        type_command="#ff5555", type_template="#bd93f9",
        type_variable="#50fa7b", disabled="#6272a4",
        preview_present="#ff79c6", preview_missing="#ff5555",
        picker_header="#ff79c6",
    ),
    "gruvbox": dict(
        text="#ebdbb2", muted="#928374", accent="#fe8019",
        accent_text="#282828", tree_bg="#3c3836", hover="#504945",
        type_command="#fb4934", type_template="#83a598",
        type_variable="#b8bb26", disabled="#928374",
        preview_present="#fe8019", preview_missing="#fb4934",
        picker_header="#fe8019",
    ),
    "high_contrast": dict(
        text="#ffffff", muted="#c0c0c0", accent="#ffd700",
        accent_text="#000000", tree_bg="#1a1a1a", hover="#333333",
        type_command="#ff6b6b", type_template="#79b8ff",
        type_variable="#7ee787", disabled="#8b949e",
        preview_present="#ffd700", preview_missing="#ff6b6b",
        picker_header="#ffd700",
    ),
    "sepia": dict(
        text="#4a4238", muted="#8a7f70", accent="#c2410c",
        accent_text="#fffaf0", tree_bg="#fffaf0", hover="#efe4c8",
        type_command="#b91c1c", type_template="#1d4ed8",
        type_variable="#15803d", disabled="#8a7f70",
        preview_present="#c2410c", preview_missing="#be123c",
        picker_header="#c2410c",
    ),
}

THEME_ORDER = [
    ("dark", "Dark"),
    ("light", "Light"),
    ("mocha", "Catppuccin Mocha"),
    ("nord", "Nord"),
    ("dracula", "Dracula"),
    ("gruvbox", "Gruvbox Dark"),
    ("high_contrast", "High Contrast"),
    ("sepia", "Sepia"),
]

STYLESHEETS = {
    "dark": dark_stylesheet,
    "light": light_stylesheet,
    "mocha": mocha_stylesheet,
    "nord": nord_stylesheet,
    "dracula": dracula_stylesheet,
    "gruvbox": gruvbox_stylesheet,
    "high_contrast": high_contrast_stylesheet,
    "sepia": sepia_stylesheet,
}
