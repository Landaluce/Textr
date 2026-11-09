"""Guard Textr's desktop identity against other Python Qt launchers.

Qt derives the X11 WM_CLASS instance from argv[0], so a source run advertises
"main.py" unless told otherwise. Any launcher on the machine that declares
StartupWMClass=main.py (Aegis does) then outranked textr.desktop and the dock
drew the wrong logo. These tests pin both halves of the fix: the class the
window advertises and the class the installed entry claims.
"""

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import main
import shortcut


def test_window_class_is_specific_and_matches_entry_file_name():
    # The shell also matches a launcher by its file name (textr.desktop), so
    # the class and the file stem have to agree for the icon to resolve.
    assert shortcut.DESKTOP_FILE_NAME == f"{shortcut.WINDOW_CLASS}.desktop"
    assert shortcut.WINDOW_CLASS != "main.py"


def test_qt_argv_advertises_textrs_own_window_class():
    argv = main._qt_argv(["main.py", "--debug"])
    if sys.platform.startswith("linux"):
        assert argv == ["main.py", "-name", shortcut.WINDOW_CLASS, "--debug"]
    else:
        assert argv == ["main.py", "--debug"]


def test_desktop_entry_claims_the_window_class():
    entry = shortcut._desktop_contents("/usr/bin/textr")
    lines = entry.splitlines()
    assert f"StartupWMClass={shortcut.WINDOW_CLASS}" in lines
    assert "StartupWMClass=main.py" not in lines
    assert f"Icon={shortcut.ICON_THEME_NAME}" in lines
