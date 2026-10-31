import sys
import threading
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication
from daemon import Daemon
from ui import TexterUI
from stylesheet import STYLESHEETS


def main():
    app = QApplication(sys.argv)
    
    # Saved theme (Dark by default); the manager's Theme combo in
    # Preferences changes it live and persists it here.
    theme = QSettings("Landaluce", "Textr").value("theme", "dark")
    app.setStyleSheet(STYLESHEETS.get(theme, STYLESHEETS["dark"]))

    daemon = Daemon()
    window = TexterUI(daemon=daemon)
    window.set_app(app)  # Pass QApplication to UI
    window.show()

    daemon_thread = threading.Thread(target=daemon.run, daemon=True)
    daemon_thread.start()

    sys.exit(app.exec())

if __name__ == "__main__":
    main()