import sys
import threading
from PySide6.QtWidgets import QApplication
from daemon import Daemon
from ui import TexterUI
from stylesheet import dark_stylesheet


def main():
    app = QApplication(sys.argv)
    
    # Sleek Developer Dark Mode QSS Stylesheet
    app.setStyleSheet(dark_stylesheet)

    daemon = Daemon()
    window = TexterUI(daemon=daemon)
    window.set_app(app)  # Pass QApplication to UI
    window.show()

    daemon_thread = threading.Thread(target=daemon.run, daemon=True)
    daemon_thread.start()

    sys.exit(app.exec())

if __name__ == "__main__":
    main()