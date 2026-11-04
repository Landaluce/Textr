import sys
import threading
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication
from app_icon import apply_app_icon
from daemon import Daemon
from ui import TexterUI
from stylesheet import STYLESHEETS, resolved_theme


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Textr")
    # No window icon was ever set, so the dock showed a generic gear.
    # The embedded logo is applied to the application and to the main
    # window so the dock, taskbar and Alt-Tab show the Textr mark.
    apply_app_icon(app)
    
    # Saved theme (Dark by default); the manager's Theme combo in
    # Preferences changes it live and persists it here.
    theme = QSettings("Landaluce", "Textr").value("theme", "dark")
    app.setStyleSheet(STYLESHEETS[resolved_theme(theme)])

    daemon = Daemon()
    window = TexterUI(daemon=daemon)
    if not app.windowIcon().isNull():
        window.setWindowIcon(app.windowIcon())
    window.set_app(app)  # Pass QApplication to UI
    window.show()

    daemon_thread = threading.Thread(target=daemon.run, daemon=True)
    daemon_thread.start()

    sys.exit(app.exec())

if __name__ == "__main__":
    main()