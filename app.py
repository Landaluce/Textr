import sys
from PySide6.QtWidgets import QApplication
from daemon import Daemon
from ui import TexterUI

def main():
    app = QApplication(sys.argv)
    daemon = Daemon()
    window = TexterUI(daemon=daemon)
    window.show()

    import threading
    daemon_thread = threading.Thread(target=daemon.run, daemon=True)
    daemon_thread.start()

    sys.exit(app.exec())

if __name__ == "__main__":
    main()