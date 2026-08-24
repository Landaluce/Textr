from datetime import datetime
import time
import re
import pyperclip
import json
import os
import random
import math
import shutil
import string
import subprocess
import threading
import traceback
from pynput import keyboard

# Modifier name -> key for the configurable paste shortcut (see Daemon._paste_hotkey_parts).
_PASTE_MODIFIERS = {
    "ctrl": keyboard.Key.ctrl,
    "leftctrl": keyboard.Key.ctrl,
    "shift": keyboard.Key.shift,
    "leftshift": keyboard.Key.shift,
    "alt": keyboard.Key.alt,
    "leftalt": keyboard.Key.alt,
}


class Daemon:
    def __init__(self, snippets_file="snippets.json"):
        self.writing_mode = False
        self.debug = False
        self.debugging = False
        self.buffer = ""
        self.max_buffer = 100
        self.running = True
        self.enabled = True
        self.is_expanding = False
        # Native Wayland sessions can't use pynput's X11 backend; typing
        # output then falls back to ydotool (see _wayland_replace).
        self.wayland = self.is_wayland()
        self.current_language = "python"
        self.listener = None
        self.hotkey_listener = None
        self.wayland_input = None
        self.controller = keyboard.Controller()
        self.snippets_file = self._resolve_path(snippets_file)
        self.snippets = {}
        # Guards all access to self.snippets: the UI thread mutates it while
        # the keyboard listener iterates it on every keystroke.
        self.snippets_lock = threading.RLock()
        # Keys that invalidate the typed buffer (cursor jumps, line edits...).
        # Normalized names shared by the pynput and evdev backends.
        self.buffer_reset_names = {
            "enter", "tab", "esc", "delete", "home", "end",
            "page_up", "page_down", "up", "down", "left", "right",
        }
        # When the buffer ends with a trigger that is also the prefix of a
        # longer trigger (:d vs :dt), expansion is deferred until the next key
        # reveals which one the user meant (see _handle_key).
        self.pending_trigger = None
        self.pending_buffer_len = 0
        self.load_snippets()
        self.load_config()

    def _resolve_path(self, filename):
        """Locate a data file, preferring XDG config with a legacy fallback.

        Precedence: absolute paths and the TEXTR_CONFIG_DIR override (used by
        tests) win; otherwise a file already present next to the working
        directory (legacy source-tree layout) is used as-is; otherwise the
        file lives under $XDG_CONFIG_HOME/textr (created on demand).
        """
        if os.path.isabs(filename):
            return filename
        override = os.environ.get("TEXTR_CONFIG_DIR")
        if override:
            return os.path.join(override, filename)
        if os.path.exists(filename):
            return filename
        xdg_config = os.environ.get("XDG_CONFIG_HOME") or os.path.join(
            os.path.expanduser("~"), ".config"
        )
        config_dir = os.path.join(xdg_config, "textr")
        os.makedirs(config_dir, exist_ok=True)
        return os.path.join(config_dir, filename)

    def load_config(self):
        self.config_file = self._resolve_path("config.json")
        self.config = {
            "name": "Alvaro",
            "email": "alvaro@example.com",
            # Keyboard layout used by the evdev Wayland listener (input_wayland.py).
            "layout": "us",
            # Language used for template snippets; persisted so it survives restarts.
            "language": "python",
            # Shortcut used to paste expansions in clipboard mode. Some
            # terminals expect a different combo than Ctrl+Shift+V.
            "paste_hotkey": "ctrl+shift+v",
            # strftime formats for {date}/{time} and the :d/:time/:datetime
            # commands; configurable in the UI profile settings.
            "date_format": "%Y-%m-%d",
            "time_format": "%H:%M",
        }
        if os.path.exists(self.config_file):
            try:
                with open(self.config_file, "r") as f:
                    self.config.update(json.load(f))
                print("Loaded user settings from config.json")
            except Exception as e:
                print("Failed to load config:", e)
        self.current_language = self.config.get("language", self.current_language)

    def set_language(self, language):
        """Switch the template language and persist it as the default."""
        self.current_language = language
        self.config["language"] = language
        self.save_config()

    def save_config(self):
        try:
            with open(self.config_file, "w") as f:
                json.dump(self.config, f, indent=2)
            print("Saved user settings to config.json")
        except Exception as e:
            print("Failed to save config:", e)

    def load_snippets(self):
        if os.path.exists(self.snippets_file):
            try:
                with open(self.snippets_file, "r") as f:
                    loaded = json.load(f)

                with self.snippets_lock:
                    for trigger, snippet in loaded.items():
                        if isinstance(snippet, dict) and "tags" not in snippet:
                            # Legacy snippets only had a category; treat it as
                            # their primary tag so tag-grouping still works.
                            category = snippet.get("category", "")
                            snippet["tags"] = (
                                [category] if category and category != "uncategorized" else []
                            )
                    self.snippets.update(loaded)
                print(f"Loaded {len(loaded)} snippets from {self.snippets_file}")
            except Exception as e:
                print(f"Failed to load snippets: {e}")

    def save_snippets(self):
        try:
            with self.snippets_lock:
                items = list(self.snippets.items())

            snippets_to_save = {}

            for trigger, snippet in items:
                clean_snippet = {}

                for k, v in snippet.items():
                    if callable(v):
                        continue
                    clean_snippet[k] = v

                snippets_to_save[trigger] = clean_snippet

            with open(self.snippets_file, "w") as f:
                json.dump(snippets_to_save, f, indent=2)

            print(f"Saved {len(snippets_to_save)} snippets to {self.snippets_file}")
        except Exception as e:
            print(f"Failed to save snippets: {e}")

    def add_or_update_snippet(self, trigger, snippet):
        with self.snippets_lock:
            self.snippets[trigger] = snippet
        self.save_snippets()

    def replace_snippet(self, old_trigger, new_trigger, snippet):
        with self.snippets_lock:
            self.snippets.pop(old_trigger, None)
            self.snippets[new_trigger] = snippet
        self.save_snippets()

    def delete_snippet(self, trigger):
        with self.snippets_lock:
            self.snippets.pop(trigger, None)
        self.save_snippets()

    def import_snippets(self, snippets):
        with self.snippets_lock:
            self.snippets.update(snippets)
        self.save_snippets()

    def run_command(self, item):
        action = item.get("action")

        if action == "set_language":
            self.set_language(item.get("value"))
            print("Language switched to:", self.current_language)

        elif action == "shutdown":
            print("Shutting down Texter daemon...")
            self.running = False
            self.save_snippets()
            self._stop_input()
            
            # Signal UI to quit if it exists
            if hasattr(self, 'ui') and self.ui:
                self.ui.quit_requested.emit()

        elif action == "toggle_pause":
            self.enabled = not self.enabled
            print("Texter enabled =", self.enabled)

        elif action == "resume":
            self.enabled = True
            print("Texter resumed")

        elif action == "ui":
            print("Bringing UI to front...")
            if hasattr(self, "ui") and self.ui:
                self.ui.daemon_command.emit("ui")
        
        elif action == "hide":
            if hasattr(self, "ui") and self.ui:
                self.ui.daemon_command.emit("hide")

        elif action == "reload":
            self.load_snippets()
            print("Reloaded snippets from disk")
            if hasattr(self, "ui") and self.ui:
                self.ui.daemon_command.emit("refresh")

        elif action == "save":
            self.save_snippets()
            self.save_config()
            print("Saved snippets and config")

        elif action == "top":
            if hasattr(self, "ui") and self.ui:
                self.ui.daemon_command.emit("top")

        elif action == "restart":
            print("Restarting Texter...")
            self.save_snippets()
            self.save_config()
            if hasattr(self, "ui") and self.ui:
                self.ui.daemon_command.emit("restart")

        elif action == "toggle_writing_mode":
            self.writing_mode = not self.writing_mode
            print("Writing mode =", self.writing_mode)
            if hasattr(self, "ui") and self.ui:
                self.ui.daemon_command.emit("sync_writing_mode")

    def quit_app(self):
        """Quit the entire application"""
        print("Daemon: Quitting application...")
        self.running = False
        self.save_snippets()
        self._stop_input()
        import sys
        sys.exit(0)

    def get_categories(self):
        """Get all unique categories from snippets"""
        with self.snippets_lock:
            items = list(self.snippets.values())
        categories = set()
        for snippet in items:
            cat = snippet.get("category", "uncategorized")
            categories.add(cat)
        return sorted(categories)

    def get_snippets_by_category(self, category):
        """Get snippets filtered by category"""
        with self.snippets_lock:
            items = list(self.snippets.items())
        if category == "all":
            return dict(items)
        return {
            trigger: snippet
            for trigger, snippet in items
            if snippet.get("category", "uncategorized") == category
        }
    
    def set_category(self, category):
        """Set the currently selected category"""
        self.selected_category = category

    def get_context(self):
        return {
            "name": self.config.get("name", "Alvaro"),
            "email": self.config.get("email", "alvaro@example.com"),
            "date": datetime.now().strftime(self.config.get("date_format", "%Y-%m-%d")),
            "time": datetime.now().strftime(self.config.get("time_format", "%H:%M")),
            "iso": datetime.now().isoformat(timespec="seconds"),
            "week": datetime.now().strftime("%V"),
        }

    def render_template(self, text):
        context = self.get_context()
        try:
            context["clipboard"] = pyperclip.paste()
        except Exception:
            context["clipboard"] = ""

        def replace_var(match):
            key = match.group(1)
            return context.get(key, f"{{{key}}}")

        return re.sub(r"\{(\w+)\}", replace_var, text)

    def replace(self, trigger, expansion):
        # {cursor} marks where the caret should end up: the text after it is
        # typed/pasted first, then the caret is moved back to that position.
        parts = expansion.split("{cursor}", 1)
        if len(parts) == 2:
            before, after = parts
            final_text = before + after
            move_back = len(after)
        else:
            final_text = expansion
            move_back = 0

        old_clipboard = ""
        try:
            old_clipboard = pyperclip.paste()
        except Exception:
            pass

        try:
            self.is_expanding = True
            time.sleep(0.03)

            if self.wayland:
                self._wayland_replace(trigger, final_text, move_back)
            else:
                # Delete the trigger with human-like backspace timing
                for _ in range(len(trigger)):
                    self.controller.press(keyboard.Key.backspace)
                    self.controller.release(keyboard.Key.backspace)
                    time.sleep(random.uniform(0.04, 0.12))  # variable backspace speed

                time.sleep(random.uniform(0.08, 0.25))  # natural pause before typing

                if self.writing_mode:
                    self._type_humanlike(final_text)
                else:
                    # Paste mode with jitter
                    pyperclip.copy(final_text)
                    time.sleep(random.uniform(0.05, 0.09))
                    mods, key = self._paste_hotkey_parts(
                        self.config.get("paste_hotkey", "ctrl+shift+v")
                    )
                    for mod in mods:
                        self.controller.press(mod)
                    self.controller.press(key)
                    self.controller.release(key)
                    for mod in reversed(mods):
                        self.controller.release(mod)
                    time.sleep(random.uniform(0.3, 0.8))

                # Move the caret back to the {cursor} position
                for _ in range(move_back):
                    self.controller.press(keyboard.Key.left)
                    self.controller.release(keyboard.Key.left)
                    time.sleep(0.02)

                # Restore original clipboard
                if old_clipboard:
                    try:
                        pyperclip.copy(old_clipboard)
                    except Exception:
                        pass

        except Exception as e:
            print("REPLACE ERROR:", repr(e))

        finally:
            self.buffer = ""
            self.is_expanding = False

    @staticmethod
    def is_wayland():
        """Detect a native Wayland session (pynput's backend is X11-only)."""
        return (
            os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland"
            or bool(os.environ.get("WAYLAND_DISPLAY"))
        )

    def _ydotool_available(self):
        """Check (once) whether the ydotool client is installed."""
        if not hasattr(self, "_ydotool_checked"):
            self._ydotool_checked = True
            self._ydotool_ok = shutil.which("ydotool") is not None
            if not self._ydotool_ok:
                print("ydotool not found: Wayland typing output will not work.")
                print("Install the ydotool daemon: https://github.com/ReimuNotMoe/ydotool")
        return self._ydotool_ok

    def _wayland_run(self, args):
        """Run a ydotool command, logging failures once instead of spamming."""
        try:
            result = subprocess.run(args, capture_output=True, text=True)
            if result.returncode != 0 and not getattr(self, "_wayland_err_printed", False):
                self._wayland_err_printed = True
                print(f"ydotool error ({' '.join(args)}): {result.stderr.strip()}")
        except Exception as e:
            print("ydotool failed:", e)

    def _press_backspace(self):
        """Delete one character, using the backend appropriate to the session."""
        if self.wayland:
            self._wayland_run(["ydotool", "key", "14"])  # KEY_BACKSPACE
        else:
            self.controller.press(keyboard.Key.backspace)
            self.controller.release(keyboard.Key.backspace)

    def _wayland_replace(self, trigger, final_text, move_back):
        """Best-effort output on Wayland via ydotool.

        pynput cannot capture global keys on native Wayland, so trigger
        detection only works for XWayland apps. When it does fire, use
        ydotool to type instead of the X11 controller + clipboard."""
        if not self._ydotool_available():
            print("REPLACE ERROR: cannot type on Wayland (ydotool missing)")
            return

        for _ in range(len(trigger)):
            self._press_backspace()
            time.sleep(random.uniform(0.04, 0.12))

        time.sleep(random.uniform(0.08, 0.25))

        self._wayland_run(["ydotool", "type", "--key-delay", "12", final_text])

        for _ in range(move_back):
            self._wayland_run(["ydotool", "key", "105"])  # KEY_LEFT
            time.sleep(0.02)


    def _paste_hotkey_parts(self, combo):
        """Parse a 'ctrl+shift+v'-style combo into (modifier keys, final key).

        The final key may be a single character (e.g. "v") or a named key
        (e.g. "insert", "enter"). Unknown modifiers fall back to Ctrl so a
        typo in config still produces a working paste shortcut.
        """
        parts = [p.strip().lower() for p in (combo or "").split("+") if p.strip()]
        if not parts:
            parts = ["ctrl", "shift", "v"]
        mods = []
        for name in parts[:-1]:
            mods.append(_PASTE_MODIFIERS.get(name, keyboard.Key.ctrl))
        key_name = parts[-1]
        key = getattr(keyboard.Key, key_name, None)
        if key is None:
            key = key_name  # single characters ("v") work directly with the controller
        return mods, key

    def _type_humanlike(self, text):
        """
        Types text with statistically human-like keystroke dynamics.
        Bypasses detection by modeling:
        - Variable inter-key latency (60-250ms, lognormal-ish distribution)
        - Hand-alternation speed boost (faster when alternating hands)
        - Bigram-specific timing (common pairs like 'th', 'er' are faster)
        - Occasional typos + corrections (1-3% of keystrokes)
        - Burst patterns (3-7 chars fast, then micro-pause)
        - Realistic hold times per key
        """
        # Common English bigrams that are typed faster (muscle memory)
        fast_bigrams = {
            "th", "he", "in", "er", "an", "re", "ed", "on", "es", "st",
            "en", "at", "to", "nt", "ha", "nd", "ou", "ea", "ng", "or",
            "ti", "as", "te", "et", "hi", "of", "it", "is", "le", "ar",
            "co", "me", "de", "se", "si", "ma", "pa", "ta", "el", "al",
            "ll", "tt", "ss", "ee", "oo", "pp", "rr", "nn", "cc", "dd",
        }

        # Left-hand / right-hand key sets for hand-alternation modeling
        left_hand = set("qwertasdfgzxcvb")
        right_hand = set("yuiophjklnm")
        # Note: This is simplified. Real hand-alternation also considers
        # which finger, but this is good enough.

        i = 0
        text_len = len(text)
        chars_typed_this_burst = 0
        burst_length = random.randint(3, 8)  # 3-8 chars per burst

        while i < text_len:
            ch = text[i]

            # --- 1. Hold time (key down duration) ---
            # Realistic hold: 40-120ms, varies by key
            hold_time = random.uniform(0.045, 0.110)

            # --- 2. Inter-key interval (latency to next key) ---
            if i < text_len - 1:
                next_ch = text[i + 1]
                bigram = ch + next_ch

                # Base latency
                if bigram in fast_bigrams:
                    base_latency = random.uniform(0.050, 0.120)
                elif (ch in left_hand and next_ch in right_hand) or \
                    (ch in right_hand and next_ch in left_hand):
                    # Hand alternation is faster
                    base_latency = random.uniform(0.045, 0.110)
                elif ch == next_ch:
                    # Double letters are faster
                    base_latency = random.uniform(0.040, 0.095)
                else:
                    # Same hand, different finger
                    base_latency = random.uniform(0.080, 0.200)

                # Add lognormal-ish noise (skewed right, like real data)
                noise = random.lognormvariate(0, 0.35) * 0.015
                latency = base_latency + noise

                # Clamp to realistic range
                latency = max(0.035, min(0.280, latency))
            else:
                latency = random.uniform(0.060, 0.200)

            # --- 3. Burst pattern: micro-pause after every burst ---
            chars_typed_this_burst += 1
            if chars_typed_this_burst >= burst_length:
                latency += random.uniform(0.080, 0.350)  # micro-pause
                chars_typed_this_burst = 0
                burst_length = random.randint(3, 8)

            # --- 4. Typo injection (1.5-4% probability) ---
            # Only inject typos that are plausible (adjacent key on QWERTY)
            typo_roll = random.random()
            if typo_roll < 0.025 and i < text_len:  # ~2.5% typo rate
                # Generate a plausible typo
                typo_char = self._generate_typo(ch)
                if typo_char and typo_char != ch:
                    # Type the wrong character
                    self.controller.press(typo_char)
                    self.controller.release(typo_char)
                    time.sleep(random.uniform(0.020, 0.040))

                    # Press backspace to correct
                    self.controller.press(keyboard.Key.backspace)
                    self.controller.release(keyboard.Key.backspace)
                    time.sleep(random.uniform(0.080, 0.220))  # pause after correction

            # --- 5. Type the actual character ---
            self.controller.press(ch)

            # Hold time (key held down)
            time.sleep(hold_time)

            self.controller.release(ch)

            # --- 6. Inter-key delay before next character ---
            time.sleep(latency)

            i += 1

        # --- 7. Ending flourish: natural pause after finishing ---
        time.sleep(random.uniform(0.100, 0.400))


    def _generate_typo(self, ch):
        """Generate a plausible typo based on QWERTY adjacency."""
        if ch not in string.ascii_lowercase:
            return None

        qwerty_adjacent = {
            'q': ['w', 'a'],
            'w': ['q', 'e', 's'],
            'e': ['w', 'r', 'd'],
            'r': ['e', 't', 'f'],
            't': ['r', 'y', 'g'],
            'y': ['t', 'u', 'h'],
            'u': ['y', 'i', 'j'],
            'i': ['u', 'o', 'k'],
            'o': ['i', 'p', 'l'],
            'p': ['o'],
            'a': ['s', 'q', 'w'],
            's': ['a', 'd', 'w', 'e'],
            'd': ['s', 'f', 'e', 'r'],
            'f': ['d', 'g', 'r', 't'],
            'g': ['f', 'h', 't', 'y'],
            'h': ['g', 'j', 'y', 'u'],
            'j': ['h', 'k', 'u', 'i'],
            'k': ['j', 'l', 'i', 'o'],
            'l': ['k', 'o', 'p'],
            'z': ['x', 's'],
            'x': ['z', 'c', 's', 'd'],
            'c': ['x', 'v', 'd', 'f'],
            'v': ['c', 'b', 'f', 'g'],
            'b': ['v', 'n', 'g', 'h'],
            'n': ['b', 'm', 'h', 'j'],
            'm': ['n', 'j', 'k'],
        }

        adj = qwerty_adjacent.get(ch, [])
        if not adj:
            return None
        return random.choice(adj)

    def is_valid_trigger(self, trigger):
        with self.snippets_lock:
            item = self.snippets.get(trigger)
        if not isinstance(item, dict):
            return None

        item_type = item.get("type")

        if item_type == "command":
            action = item.get("action")
            if action in ("date", "time", "datetime"):
                # Text-inserting commands expand in the active window like a
                # normal snippet instead of routing through the manager UI.
                # Honor the user's configured date/time formats.
                date_fmt = self.config.get("date_format", "%Y-%m-%d")
                time_fmt = self.config.get("time_format", "%H:%M")
                fmt = {
                    "date": date_fmt,
                    "time": time_fmt,
                    "datetime": f"{date_fmt} {time_fmt}",
                }[action]
                return datetime.now().strftime(fmt)
            self.run_command(item)
            for _ in range(len(trigger)):
                self._press_backspace()
            return None

        if item_type == "variable":
            return self.render_template(item.get("value", ""))

        if item_type == "template":
            raw = item.get("languages", {}).get(self.current_language)
            if not raw:
                return None
            return self.render_template(raw)

        return None

    def on_press(self, key):
        # Never let an exception escape into an input-backend thread (pynput
        # listener or evdev reader): that would silently kill the listener and
        # stop all trigger detection.
        try:
            return self._handle_key(key)
        except Exception:
            print("ERROR: exception in key handler (listener kept alive):")
            traceback.print_exc()
            return True

    BOUNDARY_CHARS = ' \t\n.,;:!?"\'()-_[]{}<>/\\'

    def _is_trigger_complete(self, buffer, trigger):
        """True if `buffer` ends with `trigger` starting at a word boundary."""
        if not buffer.endswith(trigger):
            return False
        if len(buffer) == len(trigger):
            return True  # just typed the trigger
        return buffer[-(len(trigger) + 1)] in self.BOUNDARY_CHARS

    def _enabled(self, snippet):
        """A snippet is active unless it has "enabled": false."""
        return not isinstance(snippet, dict) or snippet.get("enabled", True)

    def _has_longer_prefix(self, trigger):
        """True if an enabled longer trigger starts with `trigger`.

        Disabled snippets never participate in prefix deferral, so a
        disabled ':dt' no longer holds back an enabled ':d'.
        """
        with self.snippets_lock:
            for other, snippet in self.snippets.items():
                if (
                    len(other) > len(trigger)
                    and other.startswith(trigger)
                    and self._enabled(snippet)
                ):
                    return True
        return False

    def _pending_extends(self, extra):
        """Can the deferred trigger still grow into a longer trigger?

        `extra` is everything the user typed after the deferred trigger; the
        deferred trigger only survives if a longer trigger starts with
        trigger + extra.
        """
        candidate = self.pending_trigger + extra
        with self.snippets_lock:
            for other, snippet in self.snippets.items():
                if (
                    len(other) > len(self.pending_trigger)
                    and other.startswith(candidate)
                    and self._enabled(snippet)
                ):
                    return True
        return False

    def _record_usage(self, trigger):
        """Count an expansion so the UI can sort by recent/most used."""
        with self.snippets_lock:
            snippet = self.snippets.get(trigger)
            if isinstance(snippet, dict):
                snippet["usage"] = int(snippet.get("usage", 0)) + 1
                snippet["last_used"] = datetime.now().isoformat(timespec="seconds")

    def _expand(self, trigger):
        """Resolve and expand `trigger` if it yields text; buffer is cleared."""
        with self.snippets_lock:
            item = self.snippets.get(trigger)

        if item is None:
            self.buffer = ""
            return True

        # Disabled snippets never expand (but still consume the buffer).
        if not self._enabled(item):
            self.buffer = ""
            return True

        # Commands always work, even when paused (e.g. :resume, :quit).
        if item.get("type") == "command":
            expansion = self.is_valid_trigger(trigger)
            if expansion is not None:
                self._record_usage(trigger)
                self.replace(trigger, expansion)
            return True

        # Non-commands only expand when not paused.
        if not self.enabled:
            self.buffer = ""
            return True

        expansion = self.is_valid_trigger(trigger)
        if expansion is not None:
            self._record_usage(trigger)
            self.replace(trigger, expansion)
        return True

    def _fire_pending(self):
        """Expand the deferred trigger (a boundary key or char completed it)."""
        trigger = self.pending_trigger
        self.pending_trigger = None
        if trigger is not None:
            self._expand(trigger)

    def _handle_key(self, key):
        if self.debugging and self.debug:
            print(f"DEBUG: Key pressed: {key}, Buffer: '{self.buffer}'")

        if self.is_expanding:
            return True

        name = getattr(key, "name", None)
        char = getattr(key, "char", None)

        if name == "backspace":
            # Deleting breaks a deferred trigger just like the buffer.
            self.pending_trigger = None
            self.buffer = self.buffer[:-1]
            return True

        if name is not None:
            if self.pending_trigger is not None and name in self.buffer_reset_names:
                # A boundary key (Enter, Tab...) finishes the deferred word.
                self._fire_pending()
            elif name in self.buffer_reset_names:
                self.buffer = ""
            return True

        if not char:
            return True

        # A boundary character finishes the deferred word -> fire it now.
        if self.pending_trigger is not None and char in self.BOUNDARY_CHARS:
            self._fire_pending()
            return True

        self.buffer += char
        self.buffer = self.buffer[-self.max_buffer:]

        with self.snippets_lock:
            sorted_triggers = sorted(
                (t for t, s in self.snippets.items() if self._enabled(s)),
                key=len,
                reverse=True,
            )

        # Only one trigger can complete at a time (the buffer ends with it), so
        # the first (longest) match wins.
        completed = None
        for trigger in sorted_triggers:
            if self._is_trigger_complete(self.buffer, trigger):
                completed = trigger
                break

        if completed is not None:
            if self._has_longer_prefix(completed):
                # Wait: the user might be typing a longer trigger (:dt after :d).
                self.pending_trigger = completed
                self.pending_buffer_len = len(self.buffer)
                return True
            return self._expand(completed)

        if self.pending_trigger is not None:
            extra = self.buffer[self.pending_buffer_len:]
            if not self._pending_extends(extra):
                # e.g. ':d' followed by 'a' when only ':dt' could continue:
                # the deferred trigger is broken, not complete - drop it.
                self.pending_trigger = None

        return True

    def toggle_pause_via_hotkey(self):
        self.enabled = not self.enabled
        print("Global hotkey triggered: Texter enabled =", self.enabled)

    def _start_wayland_input(self):
        """Start evdev input capture on Wayland. Returns True on success."""
        try:
            from input_wayland import WaylandInputListener
        except ImportError:
            print("input_wayland not importable: Wayland capture unavailable.")
            return False
        self.wayland_input = WaylandInputListener(
            on_key=self.on_press,
            on_hotkey=self.toggle_pause_via_hotkey,
            layout=self.config.get("layout", "us"),
        )
        return self.wayland_input.start()

    def run(self):
        if self.wayland:
            print("Wayland session detected: capturing input via evdev and")
            print("typing output via ydotool (pynput is X11-only).")
            self._ydotool_available()
            if self._start_wayland_input():
                print("Texter daemon running (Wayland + evdev mode)")
                while self.running:
                    time.sleep(1)
            else:
                print("Falling back to pynput: trigger detection limited to XWayland apps.")
                self._run_pynput()
        else:
            print("Texter daemon running (X11 mode)")
            self._run_pynput()

    def _run_pynput(self):
        # Register global hotkey listener
        try:
            self.hotkey_listener = keyboard.GlobalHotKeys({
                '<ctrl>+<shift>+p': self.toggle_pause_via_hotkey
            })
            self.hotkey_listener.daemon = True
            self.hotkey_listener.start()
            print("Global hotkey registered: Ctrl+Shift+P to toggle pause/resume")
        except Exception as e:
            print("Failed to register global hotkeys listener:", e)

        while self.running:
            self.listener = keyboard.Listener(on_press=self.on_press)
            self.listener.daemon = True
            self.listener.start()
            try:
                self.listener.join()
            except Exception:
                # The listener died unexpectedly; log it and restart so
                # trigger detection recovers instead of silently stopping.
                print("Keyboard listener crashed, restarting:")
                traceback.print_exc()
                time.sleep(0.5)
                continue
            break

    def _stop_input(self):
        """Stop every active input backend (pynput and/or evdev)."""
        if self.listener:
            try:
                self.listener.stop()
            except Exception:
                pass
        if self.hotkey_listener:
            try:
                self.hotkey_listener.stop()
            except Exception:
                pass
        if getattr(self, "wayland_input", None):
            try:
                self.wayland_input.stop()
            except Exception:
                pass

    def stop(self):
        self.running = False
        self.save_snippets()  # Save before stopping
        self._stop_input()

    
