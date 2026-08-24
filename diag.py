"""Diagnostics for Textr's Linux environment (X11 and Wayland).

Run with::

    python3 -m diag              # non-interactive report
    python3 -m diag --listen 5   # also capture keycodes from a keyboard device for 5s
    python3 -m diag --test-type  # also type a test string via ydotool (Wayland)

The report covers the things most likely to break expansion:

* session type (Wayland vs X11) and the relevant env vars
* /dev/input/event* devices: names, readability, and whether the daemon's
  evdev listener would capture them (keyboard devices, minus ydotool's)
* optional Python packages and ydotool availability
* optional live keycode capture: press keys and see the keycode -> character
  mapping under a given layout (great for spotting layout/AltGr mistakes)

The pure functions (``session_info``, ``device_info``, ``tool_check``) return
plain dicts so they are easy to test and to embed elsewhere.
"""

import argparse
import glob
import os
import shutil
import stat
import sys

try:
    import evdev
    from evdev import ecodes
except ImportError:  # pragma: no cover - non-Linux / bare installs
    evdev = None
    ecodes = None

from input_wayland import LAYOUTS, WaylandKeyHandler, _is_keyboard_device


def session_info():
    """Basic session env vars relevant to input capture."""
    return {
        "xdg_session_type": os.environ.get("XDG_SESSION_TYPE", "(unset)"),
        "wayland_display": os.environ.get("WAYLAND_DISPLAY", "(unset)"),
        "display": os.environ.get("DISPLAY", "(unset)"),
    }


def device_info():
    """Inspect /dev/input/event* devices without opening them for reading."""
    devices = []
    if evdev is None:
        return devices
    for path in sorted(glob.glob("/dev/input/event*")):
        entry = {"path": path, "readable": os.access(path, os.R_OK)}
        try:
            st = os.stat(path)
            entry["mode"] = stat.filemode(st.st_mode)
            entry["group"] = _group_of(st)
        except OSError:
            pass
        try:
            device = evdev.InputDevice(path)
            entry["name"] = device.name
            caps = device.capabilities(absinfo=False)
            entry["keyboard"] = _is_keyboard_device(device.name, caps)
            device.close()
        except Exception as exc:  # pragma: no cover - depends on local perms
            entry["error"] = str(exc)
        devices.append(entry)
    return devices


def _group_of(st):
    """Best-effort group name/number for a stat result."""
    try:
        import grp
        return grp.getgrgid(st.st_gid).gr_name
    except Exception:
        return str(st.st_gid)


def tool_check():
    """Which optional tools and packages are present."""
    pynput = pyperclip = None
    try:
        import pynput  # noqa: F401
        pynput = pynput.__version__ if hasattr(pynput, "__version__") else True
    except ImportError:
        pass
    try:
        import pyperclip  # noqa: F401
        pyperclip = True
    except ImportError:
        pass
    ydotool = shutil.which("ydotool")
    return {
        "python": sys.version.split()[0],
        "evdev": bool(evdev),
        "pynput": pynput,
        "pyperclip": pyperclip,
        "ydotool": ydotool,
        "layouts": sorted(LAYOUTS),
    }


def capture_keycodes(duration=5, layout="us", timeout=1.0):
    """Yield (keycode, value, mapped) tuples from the first keyboard device.

    Interactive: requires read access to /dev/input/event* (root or the
    ``input`` group). ``mapped`` is the character the daemon would put in
    its trigger buffer for that key under ``layout`` (None for modifiers,
    shortcuts, and unmapped keys).
    """
    if evdev is None:
        return
    handler = WaylandKeyHandler(layout=layout)
    paths = sorted(glob.glob("/dev/input/event*"))
    for path in paths:
        try:
            device = evdev.InputDevice(path)
        except Exception:
            continue
        try:
            caps = device.capabilities(absinfo=False)
        except Exception:
            caps = {}
        if not _is_keyboard_device(device.name, caps):
            device.close()
            continue
        print(f"Listening on {path} ({device.name}) for {duration}s "
              f"[layout: {layout}] - press some keys...")
        try:
            for event in device.read_loop():
                if event.type != ecodes.EV_KEY:
                    continue
                if event.value == 0:  # ignore releases for the report
                    continue
                result = handler.on_key(event.code, event.value)
                mapped = result.char if result is not None else None
                yield (event.code, event.value, mapped)
        except OSError:
            pass
        finally:
            try:
                device.close()
            except Exception:
                pass
        break  # only the first keyboard device


def render_report(info, devices, tools, listen=0, layout="us", test_type=False):
    """Render the diagnostics report as text (the CLI's main output)."""
    lines = []
    s = info
    lines.append(f"Session type   : {s['xdg_session_type']}")
    lines.append(f"WAYLAND_DISPLAY: {s['wayland_display']}")
    lines.append(f"DISPLAY        : {s['display']}")
    lines.append("")
    lines.append("Input devices (/dev/input/event*):")
    if not devices:
        lines.append("  (none found - python-evdev not installed, or no devices)")
    for d in devices:
        name = d.get("name", "?")
        if d.get("error"):
            kb = "type unknown (cannot open)"
        else:
            kb = "keyboard" if d.get("keyboard") else "not-keyboard"
        mode = d.get("mode", "?")
        group = d.get("group", "?")
        readable = "readable" if d.get("readable") else "NOT readable"
        extra = f" ({d['error']})" if d.get("error") else ""
        lines.append(f"  {d['path']}: {name} [{kb}, {readable}, mode {mode}, group {group}]{extra}")
    lines.append("")
    lines.append("Tools:")
    t = tools
    lines.append(f"  python    : {t['python']}")
    lines.append(f"  evdev     : {'yes' if t['evdev'] else 'MISSING (pip install evdev)'}")
    lines.append(f"  pynput    : {t['pynput'] or 'MISSING (pip install pynput)'}")
    lines.append(f"  pyperclip : {t['pyperclip'] or 'MISSING (pip install pyperclip)'}")
    lines.append(f"  ydotool   : {t['ydotool'] or 'MISSING (needed to type on Wayland)'}")
    lines.append(f"  layouts   : {', '.join(t['layouts'])}")
    if listen:
        lines.append("")
        lines.append(f"Key capture ({listen}s, layout {layout}):")
        for code, value, mapped in capture_keycodes(duration=listen, layout=layout):
            lines.append(f"  keycode {code:>3}: mapped={mapped!r}")
    if test_type:
        lines.append("")
        lines.append("ydotool type test:")
        try:
            result = _ydotool_type_test()
            ok_text = 'OK: typed "textr diag"'
            lines.append(f"  {ok_text if result else 'FAILED (see ydotool error above)'}")
        except Exception as exc:
            lines.append(f"  FAILED: {exc}")
    return "\n".join(lines)


def _ydotool_type_test():
    """Type a short test string via ydotool; returns True on success."""
    import subprocess
    result = subprocess.run(
        ["ydotool", "type", "--key-delay", "12", "textr diag"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        print(f"  ydotool error: {result.stderr.strip()}")
    return result.returncode == 0


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Textr environment diagnostics (Linux input capture)."
    )
    parser.add_argument("--listen", type=int, default=0, metavar="SECONDS",
                        help="capture keycodes from the first keyboard device")
    parser.add_argument("--layout", default="us", choices=sorted(LAYOUTS),
                        help="layout to use for the keycode capture")
    parser.add_argument("--test-type", action="store_true",
                        help="type a test string via ydotool (Wayland only)")
    args = parser.parse_args(argv)

    print(render_report(
        session_info(), device_info(), tool_check(),
        listen=args.listen, layout=args.layout, test_type=args.test_type,
    ))
    return 0


if __name__ == "__main__":
    sys.exit(main())
