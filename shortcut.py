"""Auto-install Textr's Linux app-menu shortcut on startup.

Why this exists: a Linux (ELF) binary cannot carry a file icon, and the
file manager shows a generic icon for it, for the Windows .exe and for
the macOS .zip too. The native place for the logo on Linux is the
application launcher, so the first time the app runs it installs one:

  - ~/.local/share/applications/textr.desktop  (Name=Textr, Icon=textr)
  - ~/.local/share/icons/hicolor/256x256/apps/textr.png  (the logo,
    taken from the same embedded PNG app_icon.py uses for the dock icon)

After that Textr shows up in Activities / the app grid with its logo and
can be pinned to the dock. The entry points at wherever this copy of
Textr currently lives (sys.executable for a frozen binary, else the
python + main.py command), so if the binary moves, the next launch from
the new location rewrites the shortcut to match.

Linux only: Windows already embeds the logo in the .exe and macOS in
the .app bundle, so there is nothing to install there.

Best-effort by design: the install runs at startup, rewrites a file
only when its contents changed, and never raises -- a failed install
must never stop Textr from starting.
"""

import os
import shlex
import shutil
import subprocess
import sys

DESKTOP_FILE_NAME = "textr.desktop"
ICON_FILE_NAME = "textr.png"
ICON_THEME_NAME = "textr"


def _home():
    return os.path.expanduser("~")


def _desktop_path():
    return os.path.join(
        _home(), ".local", "share", "applications", DESKTOP_FILE_NAME
    )


def _icon_path():
    return os.path.join(
        _home(), ".local", "share", "icons",
        "hicolor", "256x256", "apps", ICON_FILE_NAME,
    )


def _launch_command():
    """Command that launches this Textr the way it was started.

    Mirrors TexterUI._autostart_exec in ui.py so the shortcut and the
    autostart entry never disagree about how to start the app.
    """
    if getattr(sys, "frozen", False):
        return shlex.quote(sys.executable)
    argv0 = sys.argv[0] if sys.argv else ""
    if argv0 and not argv0.endswith(".py"):
        return shlex.quote(argv0)
    if argv0.endswith(".py"):
        return (
            f"{shlex.quote(sys.executable)} "
            f"{shlex.quote(os.path.abspath(argv0))}"
        )
    found = shutil.which("textr")
    return shlex.quote(found) if found else "textr"


def _launch_working_dir():
    """Directory the launcher must start in to read the same data files.

    Textr resolves config.json/snippets.json relative to the working
    directory and only then falls back to ~/.config/textr, while a .desktop
    launcher starts in $HOME unless it sets Path. For a source-tree run,
    point Path at the checkout that holds main.py so a menu launch uses the
    same snippets as launching from the source tree. Packaged builds keep
    their data in the per-OS location, so no Path is written for them.
    """
    if getattr(sys, "frozen", False):
        return None
    argv0 = sys.argv[0] if sys.argv else ""
    if argv0.endswith(".py"):
        return os.path.dirname(os.path.abspath(argv0))
    return None


def _desktop_contents(exec_command, working_dir=None):
    lines = [
        "[Desktop Entry]",
        "Type=Application",
        "Name=Textr",
        "Comment=Text expansion with an actual GUI",
        f"Exec={exec_command}",
    ]
    if working_dir:
        # Without this the entry runs in $HOME and resolves its data files to
        # ~/.config/textr instead of the checkout's snippets.json.
        lines.append(f"Path={working_dir}")
    lines += [
        f"Icon={ICON_THEME_NAME}",
        "Terminal=false",
        "Categories=Utility;",
        "Keywords=text expansion;snippets;",
    ]
    return "\n".join(lines) + "\n"


def _write_if_changed(path, data, mode="w"):
    """Write data to path only when it differs. Returns True on success."""
    if mode == "w":
        current = None
        if os.path.exists(path):
            with open(path, "r") as f:
                current = f.read()
        if current == data:
            return True
        with open(path, "w") as f:
            f.write(data)
    else:
        current = None
        if os.path.exists(path):
            with open(path, "rb") as f:
                current = f.read()
        if current == data:
            return True
        with open(path, "wb") as f:
            f.write(data)
    return True


def _install_icon():
    """Write the logo PNG into the hicolor icon theme. Returns True."""
    from app_icon import app_icon_png_bytes

    path = _icon_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    return _write_if_changed(path, app_icon_png_bytes(), mode="wb")


def _refresh_caches():
    """Tell the desktop about the new files. Every failure is ignored."""
    applications_dir = os.path.dirname(_desktop_path())
    icon_theme_dir = os.path.join(
        _home(), ".local", "share", "icons", "hicolor"
    )
    for argv in (
        ["update-desktop-database", applications_dir],
        ["gtk-update-icon-cache", "-f", "-t", icon_theme_dir],
    ):
        try:
            subprocess.run(argv, capture_output=True, timeout=10)
        except Exception:
            pass


def install_app_shortcut():
    """Install/update the Linux app-menu shortcut. Never raises.

    Returns True when the shortcut is installed (or this is not Linux
    and there is nothing to do -- callers must not treat False as an
    error either way).
    """
    if not sys.platform.startswith("linux"):
        return False
    try:
        desktop_path = _desktop_path()
        os.makedirs(os.path.dirname(desktop_path), exist_ok=True)
        _write_if_changed(
            desktop_path,
            _desktop_contents(_launch_command(), _launch_working_dir()),
        )
        _install_icon()
        _refresh_caches()
        return True
    except Exception as exc:  # noqa: BLE001 - startup must never fail here
        print(f"Shortcut install failed: {exc}")
        return False
