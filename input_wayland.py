"""Best-effort global keyboard capture for native Wayland via evdev.

pynput's global listener uses X11, so on a native Wayland session it can only
see keys typed into XWayland apps. This module reads raw key events straight
from ``/dev/input/event*`` so trigger detection works for native Wayland apps
too.

Requirements (best-effort: if any are missing the listener reports why and the
daemon falls back to pynput):

* ``python-evdev`` installed
* read access to the input devices (root, or your user in the ``input`` group)
* typing output still goes through ydotool (see the daemon's ``_wayland_run``)

Keyboard layouts: evdev only reports keycodes, so the character a key produces
depends on the active layout. The daemon passes the user's chosen layout (see
``Daemon._start_wayland_input``); supported tables live in :data:`LAYOUTS`.
Each entry is ``(level1, level2, level3)``: unshifted, shifted, and AltGr
(ISO_Level3_Shift, usually the right Alt key). The tables are best-effort
approximations of the common national variants and can be extended.
"""

import glob
import threading
import traceback

try:
    import evdev
    from evdev import ecodes
except ImportError:  # pragma: no cover - exercised on non-Linux / bare installs
    evdev = None
    ecodes = None

from keys import Key

# Sentinels returned by WaylandKeyHandler for global hotkeys.
PAUSE_HOTKEY = "PAUSE_HOTKEY"  # Ctrl+Shift+P toggles pause/resume
SHOW_HOTKEY = "SHOW_HOTKEY"  # Ctrl+Shift+Space summons the manager window

# Linux input event keycodes (see <linux/input-event-codes.h>).
KEY_ESC = 1
KEY_1, KEY_2, KEY_3, KEY_4, KEY_5 = 2, 3, 4, 5, 6
KEY_6, KEY_7, KEY_8, KEY_9, KEY_0 = 7, 8, 9, 10, 11
KEY_MINUS, KEY_EQUAL = 12, 13
KEY_BACKSPACE, KEY_TAB = 14, 15
KEY_Q, KEY_W, KEY_E, KEY_R, KEY_T = 16, 17, 18, 19, 20
KEY_Y, KEY_U, KEY_I, KEY_O, KEY_P = 21, 22, 23, 24, 25
KEY_LEFTBRACE, KEY_RIGHTBRACE = 26, 27
KEY_ENTER, KEY_LEFTCTRL = 28, 29
KEY_A, KEY_S, KEY_D, KEY_F, KEY_G = 30, 31, 32, 33, 34
KEY_H, KEY_J, KEY_K, KEY_L = 35, 36, 37, 38
KEY_SEMICOLON, KEY_APOSTROPHE, KEY_GRAVE = 39, 40, 41
KEY_LEFTSHIFT, KEY_BACKSLASH = 42, 43
KEY_Z, KEY_X, KEY_C, KEY_V, KEY_B = 44, 45, 46, 47, 48
KEY_N, KEY_M = 49, 50
KEY_COMMA, KEY_DOT, KEY_SLASH = 51, 52, 53
KEY_RIGHTSHIFT, KEY_LEFTALT = 54, 56
KEY_SPACE, KEY_CAPSLOCK = 57, 58
KEY_RIGHTCTRL, KEY_RIGHTALT = 97, 100
KEY_HOME, KEY_UP, KEY_PAGEUP = 102, 103, 104
KEY_LEFT, KEY_RIGHT, KEY_END, KEY_DOWN, KEY_PAGEDOWN = 105, 106, 107, 108, 109
KEY_DELETE = 111

# Physical letter rows are shared by all layouts below.
_QWERTY = {
    KEY_Q: ("q", "Q"), KEY_W: ("w", "W"), KEY_E: ("e", "E"),
    KEY_R: ("r", "R"), KEY_T: ("t", "T"), KEY_Y: ("y", "Y"),
    KEY_U: ("u", "U"), KEY_I: ("i", "I"), KEY_O: ("o", "O"),
    KEY_P: ("p", "P"),
    KEY_A: ("a", "A"), KEY_S: ("s", "S"), KEY_D: ("d", "D"),
    KEY_F: ("f", "F"), KEY_G: ("g", "G"), KEY_H: ("h", "H"),
    KEY_J: ("j", "J"), KEY_K: ("k", "K"), KEY_L: ("l", "L"),
    KEY_Z: ("z", "Z"), KEY_X: ("x", "X"), KEY_C: ("c", "C"),
    KEY_V: ("v", "V"), KEY_B: ("b", "B"), KEY_N: ("n", "N"),
}


def _with_altgr(table, altgr=None):
    """Expand a layout table to 3-tuples ``(level1, level2, level3)``.

    ``altgr`` maps keycodes to AltGr (level 3) characters; entries already
    given as 3-tuples are passed through unchanged.
    """
    altgr = altgr or {}
    result = {}
    for code, entry in table.items():
        if len(entry) == 3:
            level1, level2, level3 = entry
        else:
            level1, level2 = entry
            level3 = ""
        result[code] = (level1, level2, altgr.get(code, level3))
    return result


# Keycode -> (unshifted, shifted, altgr). Best-effort approximations of the
# common national layouts; extend freely.
LAYOUTS = {
    "us": _with_altgr({
        KEY_GRAVE: ("`", "~"),
        KEY_1: ("1", "!"), KEY_2: ("2", "@"), KEY_3: ("3", "#"),
        KEY_4: ("4", "$"), KEY_5: ("5", "%"), KEY_6: ("6", "^"),
        KEY_7: ("7", "&"), KEY_8: ("8", "*"), KEY_9: ("9", "("),
        KEY_0: ("0", ")"), KEY_MINUS: ("-", "_"), KEY_EQUAL: ("=", "+"),
        KEY_LEFTBRACE: ("[", "{"), KEY_RIGHTBRACE: ("]", "}"),
        KEY_SEMICOLON: (";", ":"), KEY_APOSTROPHE: ("'", '"'),
        KEY_BACKSLASH: ("\\", "|"),
        KEY_COMMA: (",", "<"), KEY_DOT: (".", ">"), KEY_SLASH: ("/", "?"),
        KEY_SPACE: (" ", " "),
        **_QWERTY,
    }),
    "uk": _with_altgr({
        KEY_GRAVE: ("`", "¬"),
        KEY_1: ("1", "!"), KEY_2: ("2", '"'), KEY_3: ("3", "£"),
        KEY_4: ("4", "$"), KEY_5: ("5", "%"), KEY_6: ("6", "^"),
        KEY_7: ("7", "&"), KEY_8: ("8", "*"), KEY_9: ("9", "("),
        KEY_0: ("0", ")"), KEY_MINUS: ("-", "_"), KEY_EQUAL: ("=", "+"),
        KEY_LEFTBRACE: ("[", "{"), KEY_RIGHTBRACE: ("]", "}"),
        KEY_SEMICOLON: (";", ":"), KEY_APOSTROPHE: ("'", "@"),
        KEY_BACKSLASH: ("#", "~"),
        KEY_COMMA: (",", "<"), KEY_DOT: (".", ">"), KEY_SLASH: ("/", "?"),
        KEY_SPACE: (" ", " "),
        **_QWERTY,
    }, altgr={KEY_GRAVE: "¦", KEY_4: "€"}),
    "de": _with_altgr({
        # German QWERTZ: Y and Z are swapped, umlauts live on the punctuation keys.
        KEY_GRAVE: ("^", "°"),
        KEY_1: ("1", "!"), KEY_2: ("2", '"'), KEY_3: ("3", "§"),
        KEY_4: ("4", "$"), KEY_5: ("5", "%"), KEY_6: ("6", "&"),
        KEY_7: ("7", "/"), KEY_8: ("8", "("), KEY_9: ("9", ")"),
        KEY_0: ("0", "="), KEY_MINUS: ("-", "_"),
        KEY_EQUAL: ("ß", "?"),
        KEY_Q: ("q", "Q"), KEY_E: ("e", "E"),
        KEY_Y: ("z", "Z"), KEY_Z: ("y", "Y"),
        KEY_LEFTBRACE: ("ü", "Ü"), KEY_RIGHTBRACE: ("+", "*"),
        KEY_SEMICOLON: ("ö", "Ö"), KEY_APOSTROPHE: ("ä", "Ä"),
        KEY_BACKSLASH: ("#", "'"),
        KEY_M: ("m", "M"),
        KEY_COMMA: (",", ";"), KEY_DOT: (".", ":"), KEY_SLASH: ("-", "_"),
        KEY_SPACE: (" ", " "),
        **{k: v for k, v in _QWERTY.items() if k not in (KEY_Q, KEY_E, KEY_Y, KEY_Z)},
    }, altgr={
        KEY_2: "²", KEY_3: "³", KEY_7: "{", KEY_8: "[", KEY_9: "]",
        KEY_0: "}", KEY_EQUAL: "´", KEY_Q: "@", KEY_E: "€",
        KEY_RIGHTBRACE: "~", KEY_BACKSLASH: "`", KEY_M: "µ",
    }),
    "fr": _with_altgr({
        # French AZERTY: letters are on shifted physical positions.
        KEY_GRAVE: ("²", "²"),
        KEY_1: ("&", "1"), KEY_2: ("é", "2"), KEY_3: ('"', "3"),
        KEY_4: ("'", "4"), KEY_5: ("(", "5"), KEY_6: ("-", "6"),
        KEY_7: ("è", "7"), KEY_8: ("_", "8"), KEY_9: ("ç", "9"),
        KEY_0: ("à", "0"), KEY_MINUS: (")", "°"), KEY_EQUAL: ("=", "+"),
        KEY_Q: ("a", "A"), KEY_W: ("z", "Z"), KEY_E: ("e", "E"),
        KEY_A: ("q", "Q"), KEY_Z: ("w", "W"),
        KEY_LEFTBRACE: ("^", "¨"), KEY_RIGHTBRACE: ("$", "£"),
        KEY_SEMICOLON: ("m", "M"), KEY_APOSTROPHE: ("ù", "%"),
        KEY_BACKSLASH: ("*", "µ"),
        KEY_M: (",", "?"), KEY_COMMA: (";", "."), KEY_DOT: (":", "/"),
        KEY_SLASH: ("!", "§"), KEY_SPACE: (" ", " "),
        **{k: v for k, v in _QWERTY.items()
           if k not in (KEY_Q, KEY_W, KEY_E, KEY_A, KEY_Z, KEY_M)},
    }, altgr={
        KEY_2: "~", KEY_3: "#", KEY_4: "{", KEY_5: "[", KEY_6: "|",
        KEY_7: "`", KEY_8: "\\", KEY_9: "^", KEY_0: "@", KEY_E: "€",
    }),
    "es": _with_altgr({
        KEY_GRAVE: ("º", "ª"),
        KEY_1: ("1", "!"), KEY_2: ("2", '"'), KEY_3: ("3", "·"),
        KEY_4: ("4", "$"), KEY_5: ("5", "%"), KEY_6: ("6", "&"),
        KEY_7: ("7", "/"), KEY_8: ("8", "("), KEY_9: ("9", ")"),
        KEY_0: ("0", "="), KEY_MINUS: ("'", "?"), KEY_EQUAL: ("¡", "¿"),
        KEY_LEFTBRACE: ("[", "{"), KEY_RIGHTBRACE: ("]", "}"),
        KEY_SEMICOLON: ("ñ", "Ñ"), KEY_APOSTROPHE: ("'", '"'),
        KEY_BACKSLASH: ("ç", "Ç"),
        KEY_COMMA: (",", ";"), KEY_DOT: (".", ":"), KEY_SLASH: ("-", "_"),
        KEY_SPACE: (" ", " "),
        **_QWERTY,
    }, altgr={
        KEY_1: "|", KEY_2: "@", KEY_3: "#", KEY_4: "~", KEY_5: "€",
        KEY_6: "¬", KEY_7: "{", KEY_8: "[", KEY_9: "]", KEY_0: "}",
    }),
}

# Special keys forwarded to the daemon as normalized names.
SPECIAL_NAMES = {
    KEY_ENTER: "enter",
    KEY_TAB: "tab",
    KEY_ESC: "esc",
    KEY_BACKSPACE: "backspace",
    KEY_DELETE: "delete",
    KEY_HOME: "home",
    KEY_END: "end",
    KEY_PAGEUP: "page_up",
    KEY_PAGEDOWN: "page_down",
    KEY_UP: "up",
    KEY_DOWN: "down",
    KEY_LEFT: "left",
    KEY_RIGHT: "right",
}

MOD_SHIFT = {KEY_LEFTSHIFT, KEY_RIGHTSHIFT}
MOD_CTRL = {KEY_LEFTCTRL, KEY_RIGHTCTRL}
MOD_ALT = {KEY_LEFTALT}  # left Alt is a shortcut modifier (Alt+Tab, Alt+F4)
MOD_ALTGR = {KEY_RIGHTALT}  # right Alt is AltGr (ISO_Level3_Shift) on most layouts


class WaylandKeyHandler:
    """Tracks modifier state and translates raw evdev key events to keys.

    Feed it ``(keycode, value)`` pairs where value is 0 (release), 1 (press)
    or 2 (repeat). It returns ``None`` for events to ignore, a :class:`Key`
    for printable/special keys, or :data:`PAUSE_HOTKEY` for the pause hotkey.
    """

    def __init__(self, layout="us"):
        self.layout = layout
        self.layout_map = LAYOUTS.get(layout) or LAYOUTS["us"]
        self.shift = False
        self.ctrl = False
        self.alt = False
        self.altgr = False
        self.caps = False

    def on_key(self, code, value):
        if value == 0:  # release
            if code in MOD_SHIFT:
                self.shift = False
            elif code in MOD_CTRL:
                self.ctrl = False
            elif code in MOD_ALT:
                self.alt = False
            elif code in MOD_ALTGR:
                self.altgr = False
            return None

        if value == 1 and code == KEY_CAPSLOCK:
            self.caps = not self.caps
            return None

        # value 2 (repeat) falls through like a press for non-modifier keys.
        if code in MOD_SHIFT:
            self.shift = True
            return None
        if code in MOD_CTRL:
            self.ctrl = True
            return None
        if code in MOD_ALT:
            self.alt = True
            return None
        if code in MOD_ALTGR:
            self.altgr = True
            return None

        # Global hotkeys (checked before the generic Ctrl/Alt suppression below).
        if code == KEY_P and self.ctrl and self.shift:
            return PAUSE_HOTKEY
        if code == KEY_SPACE and self.ctrl and self.shift:
            return SHOW_HOTKEY

        # With Ctrl or left-Alt held the key is a shortcut (Ctrl+C, Alt+Tab...),
        # not text - suppress it so it never pollutes the trigger buffer.
        if self.ctrl or self.alt:
            return None

        if code in SPECIAL_NAMES:
            return Key(name=SPECIAL_NAMES[code])

        entry = self.layout_map.get(code)
        if entry is None:
            return None
        unshifted, shifted, altgr = entry
        if self.altgr:
            return Key(char=altgr) if altgr else None
        use_upper = self.shift
        if unshifted.isalpha() and self.caps:
            use_upper = not use_upper  # caps lock is cancelled by shift
        return Key(char=shifted if use_upper else unshifted)


def _is_keyboard_device(name, caps):
    """True if the evdev device looks like a keyboard worth capturing.

    Excludes ydotool's virtual uinput device so the daemon never sees the keys
    it injects itself.
    """
    if not name or "ydotool" in name.lower():
        return False
    if ecodes is None:
        return False
    keys = caps.get(ecodes.EV_KEY, [])
    return ecodes.KEY_A in keys


class WaylandInputListener:
    """Captures global keyboard input from all evdev keyboard devices."""

    def __init__(self, on_key=None, on_hotkey=None, on_show_hotkey=None, layout="us"):
        self.on_key = on_key
        self.on_hotkey = on_hotkey
        self.on_show_hotkey = on_show_hotkey
        self.layout = layout
        self.handler = WaylandKeyHandler(layout=layout)
        self._threads = []  # (device path, reader thread) pairs
        self._devices = []
        self._dispatch_lock = threading.Lock()
        self._spawn_lock = threading.Lock()
        self._stopping = False

    def _keyboard_device_paths(self):
        """Paths of keyboard devices, excluding ydotool's uinput device."""
        paths = []
        if evdev is None:
            return paths
        for path in sorted(glob.glob("/dev/input/event*")):
            try:
                device = evdev.InputDevice(path)
            except Exception:
                continue
            try:
                name = device.name
                caps = device.capabilities(absinfo=False)
            except Exception:
                caps, name = {}, ""
            finally:
                try:
                    device.close()
                except Exception:
                    pass
            if _is_keyboard_device(name, caps):
                paths.append(path)
        return paths

    def start(self):
        """Open keyboard devices and start reader threads. Returns success."""
        if evdev is None:
            print("Wayland input capture unavailable: python-evdev not installed.")
            print("Install it with: pip install evdev")
            return False

        paths = self._keyboard_device_paths()
        if not paths:
            print(
                "No readable keyboard devices found. Need read access to "
                "/dev/input/event* - run as root or add your user to the "
                "`input` group and log in again."
            )
            return False

        print(f"Wayland input capture active on {len(paths)} device(s) "
              f"(layout: {self.layout})")
        self._stopping = False
        for path in paths:
            self._spawn_reader(path)
        return True

    def _spawn_reader(self, path):
        """Start a reader thread for `path` unless one is already reading it."""
        with self._spawn_lock:
            if self._stopping:
                return False
            # Drop entries whose thread already died so respawns don't accumulate.
            self._threads = [(p, t) for p, t in self._threads if t.is_alive()]
            if any(p == path for p, _ in self._threads):
                return False
            thread = threading.Thread(
                target=self._read_loop, args=(path,), daemon=True
            )
            thread.start()
            self._threads.append((path, thread))
            return True

    def healthy(self):
        """True while at least one device reader thread is alive."""
        with self._spawn_lock:
            return any(t.is_alive() for _, t in self._threads)

    def respawn(self):
        """Restart readers for devices that are no longer being read.

        Called by the daemon's watchdog when healthy() is False (a reader
        thread died - read error, device unplugged, exception in the loop).
        Re-scans /dev/input so replugged keyboards are picked up too.
        """
        started = 0
        for path in self._keyboard_device_paths():
            if self._spawn_reader(path):
                started += 1
        if started:
            print(f"Wayland input: restarted {started} device reader(s)")
        return started

    def _read_loop(self, path):
        try:
            device = evdev.InputDevice(path)
        except Exception as exc:
            print(f"Wayland input: cannot open {path}: {exc}")
            return
        self._devices.append(device)
        try:
            for event in device.read_loop():
                if event.type != ecodes.EV_KEY:
                    continue
                result = self.handler.on_key(event.code, event.value)
                if result is None:
                    continue
                if result == PAUSE_HOTKEY:
                    if self.on_hotkey:
                        self.on_hotkey()
                    continue
                if result == SHOW_HOTKEY:
                    if self.on_show_hotkey:
                        self.on_show_hotkey()
                    continue
                # Serialize dispatch: multiple devices could fire concurrently.
                with self._dispatch_lock:
                    if self.on_key:
                        self.on_key(result)
        except OSError:
            pass  # device unplugged or closed by stop()
        except Exception:
            # The reader thread is dead; the daemon's watchdog respawns it, so
            # log the reason instead of failing silently.
            print(f"Wayland input: reader for {path} stopped with an error:")
            traceback.print_exc()
        finally:
            try:
                device.close()
            except Exception:
                pass

    def stop(self):
        """Stop the reader threads (best-effort; they are daemon threads)."""
        with self._spawn_lock:
            self._stopping = True
            threads = [t for _, t in self._threads]
            self._threads = []
        for device in self._devices:
            try:
                device.close()
            except Exception:
                pass
        for thread in threads:
            thread.join(timeout=0.5)
