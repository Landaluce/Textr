import sys
import threading
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication
from app_icon import apply_app_icon
from daemon import Daemon
from shortcut import WINDOW_CLASS, install_app_shortcut
from ui import TexterUI
from stylesheet import STYLESHEETS, resolved_theme


def _qt_argv(argv):
    """argv for QApplication with Textr's own window class on X11.

    Qt takes the WM_CLASS instance from argv[0], which for a source run is
    "main.py". A launcher that declares StartupWMClass=main.py (other
    Python Qt apps on this machine do) then wins the dock icon, so the
    window passed -name with the class shortcut.py writes into the entry.
    """
    argv = list(argv)
    if sys.platform.startswith("linux"):
        argv[1:1] = ["-name", WINDOW_CLASS]
    return argv


def main():
    app = QApplication(_qt_argv(sys.argv))
    app.setApplicationName("Textr")
    # Wayland app_id / desktop-file association; same identity as the entry.
    app.setDesktopFileName(WINDOW_CLASS)
    # No window icon was ever set, so the dock showed a generic gear.
    # The embedded logo is applied to the application and to the main
    # window so the dock, taskbar and Alt-Tab show the Textr mark.
    apply_app_icon(app)

    # Linux only, best-effort, never blocks startup: install the
    # app-menu shortcut (textr.desktop + logo) so Textr appears in
    # Activities with its logo and can be pinned to the dock. A raw
    # Linux binary cannot carry a file icon, so this is the launcher
    # that carries it instead.
    install_app_shortcut()
    
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