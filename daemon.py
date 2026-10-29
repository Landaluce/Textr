from datetime import datetime, timedelta
import time
import re
import pyperclip
import json
import os
import queue
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
        # True while the manager window (or one of its dialogs) has keyboard
        # focus. Keys aimed at the app's own UI must never be treated as
        # trigger input: typing ':p' or ':q' into the snippet editor would
        # otherwise fire those control commands. Kept in sync by TexterUI via
        # QApplication.focusChanged.
        self.ui_focused = False
        self.is_expanding = False
        # Expansions run on a dedicated worker thread (started by run()) so
        # the keyboard listener never blocks on the multi-second paste/typing
        # sleeps in replace(). A blocked listener is what starves the input
        # backend (pynput's XRecord event loop / the evdev readers) and makes
        # it die after a while. Tests that drive on_press directly never start
        # the worker and expand inline instead.
        self._expansion_queue = queue.Queue()
        self._expansion_thread = None
        # Native Wayland sessions can't use pynput's X11 backend; typing
        # output then falls back to ydotool (see _wayland_replace).
        self.wayland = self.is_wayland()
        self.current_language = "python"
        self.listener = None
        self.hotkey_listener = None
        self.wayland_input = None
        self.controller = keyboard.Controller()
        self.snippets_file = self._resolve_path(snippets_file)
        # Prompt-on-expand: set by TexterUI to a callable taking a list of
        # missing placeholder names and returning a dict (or None = cancel).
        # When None (no UI, e.g. headless tests), missing placeholders keep
        # their old behaviour: they render as the literal {name}.
        self.prompt_handler = None
        self._prompt_event = None
        self._prompt_result = None
        # Window id active when the picker hotkey / a prompt started, so
        # insertion can be aimed back at it after a Textr dialog closes.
        self._picker_target_window = None
        self._prompt_target_window = None
        # Backups and trash live next to snippets.json.
        _data_dir = os.path.dirname(self.snippets_file) or "."
        self.backup_dir = os.path.join(_data_dir, "backups")
        self.trash_file = os.path.join(_data_dir, "trash.json")
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

    # ---- Backups -------------------------------------------------------
    def _backup_file(self, path, kind):
        """Copy `path` into backups/ as kind-YYYYMMDD-HHMMSS.json.

        Keeps the 10 newest backups per kind, pruning older ones. Returns
        the backup path, or None when there was nothing to back up.
        """
        try:
            if not os.path.exists(path):
                return None
            os.makedirs(self.backup_dir, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            dest = os.path.join(self.backup_dir, f"{kind}-{stamp}.json")
            n = 1
            while os.path.exists(dest):
                n += 1
                dest = os.path.join(self.backup_dir, f"{kind}-{stamp}-{n}.json")
            shutil.copyfile(path, dest)
            # Prune: keep the 10 newest per kind.
            peers = sorted(
                f for f in os.listdir(self.backup_dir)
                if f.startswith(kind + "-") and f.endswith(".json")
            )
            for old in peers[:-10]:
                try:
                    os.remove(os.path.join(self.backup_dir, old))
                except OSError:
                    pass
            return dest
        except Exception as e:
            print(f"Backup failed for {path}: {e}")
            return None

    def list_backups(self):
        """Newest-first list of {path, kind, name, mtime} backup dicts."""
        if not os.path.isdir(self.backup_dir):
            return []
        out = []
        for name in os.listdir(self.backup_dir):
            if not name.endswith(".json"):
                continue
            kind = name.split("-", 1)[0]
            if kind not in ("snippets", "config"):
                continue
            full = os.path.join(self.backup_dir, name)
            out.append({"path": full, "kind": kind, "name": name,
                        "mtime": os.path.getmtime(full)})
        return sorted(out, key=lambda b: b["mtime"], reverse=True)

    def restore_backup(self, path):
        """Replace snippets (or config) from a backup file.

        The file being replaced is itself backed up first by save_*,
        so restoring never destroys the current state permanently.
        Returns True on success.
        """
        try:
            with open(path, "r") as f:
                data = json.load(f)
        except Exception as e:
            print(f"Restore failed: {e}")
            return False
        name = os.path.basename(path)
        if name.startswith("snippets-"):
            if not isinstance(data, dict):
                return False
            with self.snippets_lock:
                self.snippets.clear()
                self.snippets.update(data)
            self.save_snippets()
            return True
        if name.startswith("config-"):
            if not isinstance(data, dict):
                return False
            self.config.update(data)
            self.save_config()
            return True
        return False

    # ---- Trash -----------------------------------------------------------
    def load_trash(self):
        if os.path.exists(self.trash_file):
            try:
                with open(self.trash_file, "r") as f:
                    data = json.load(f)
                return data if isinstance(data, dict) else {}
            except Exception:
                return {}
        return {}

    def _save_trash(self, trash):
        try:
            with open(self.trash_file, "w") as f:
                json.dump(trash, f, indent=2)
        except Exception as e:
            print(f"Failed to save trash: {e}")

    def restore_snippet(self, trigger):
        """Move a trashed snippet back into the live snippets."""
        trash = self.load_trash()
        entry = trash.pop(trigger, None)
        if entry is None:
            return False
        snippet = entry.get("snippet") if isinstance(entry, dict) else None
        if not isinstance(snippet, dict):
            return False
        with self.snippets_lock:
            self.snippets[trigger] = snippet
        self._save_trash(trash)
        self.save_snippets()
        return True

    def delete_trash_forever(self, trigger):
        trash = self.load_trash()
        if trigger in trash:
            trash.pop(trigger)
            self._save_trash(trash)
            return True
        return False

    def clear_trash(self):
        self._save_trash({})

    def save_config(self):
        self._backup_file(self.config_file, "config")
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

            self._backup_file(self.snippets_file, "snippets")
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
        """Remove a snippet, keeping it in trash.json for later restore.

        Call delete_trash_forever() to remove it permanently.
        """
        with self.snippets_lock:
            snippet = self.snippets.pop(trigger, None)
        if snippet is not None:
            trash = self.load_trash()
            trash[trigger] = {
                "snippet": snippet,
                "deleted_at": datetime.now().isoformat(timespec="seconds"),
            }
            self._save_trash(trash)
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
        custom_settings = self.config.get("user_settings", {})
        if not isinstance(custom_settings, dict):
            custom_settings = {}
        return {
            **custom_settings,
            "name": self.config.get("name", "Alvaro"),
            "email": self.config.get("email", "alvaro@example.com"),
            "date": datetime.now().strftime(self.config.get("date_format", "%Y-%m-%d")),
            "time": datetime.now().strftime(self.config.get("time_format", "%H:%M")),
            "iso": datetime.now().isoformat(timespec="seconds"),
            "week": datetime.now().strftime("%V"),
        }

    # Placeholders that are always resolved by the daemon itself and must
    # never be treated as custom settings or prompted for.
    BUILTIN_PLACEHOLDERS = frozenset(
        {"name", "email", "date", "time", "iso", "week", "clipboard", "cursor"}
    )

    def missing_placeholders(self, text):
        """Placeholder names in `text` with no (non-empty) value.

        Built-ins and {cursor} are excluded. A name already in
        user_settings with a non-empty value is not missing.
        """
        context = self.get_context()
        missing = []
        for key in re.findall(r"\{([A-Za-z_][A-Za-z0-9_]*)(?:[+-]\d+)?\}", text):
            if key in self.BUILTIN_PLACEHOLDERS:
                continue
            value = context.get(key)
            if value is None or (isinstance(value, str) and not value.strip()):
                if key not in missing:
                    missing.append(key)
        return missing

    def render_template(self, text, prompt_values=None):
        """Render placeholders, with relative dates and prompt values.

        Relative dates: {date+7} / {date-3} shift by days (strftime with
        the configured date format); {iso+7} shifts by days; {time+2}
        shifts by hours; {week+1} shifts by weeks. Unknown keys, and
        offset keys that are not date/time/iso/week, render unchanged.
        prompt_values (from prompt-on-expand) wins over a custom setting
        for non-built-in keys.
        """
        context = self.get_context()
        try:
            context["clipboard"] = pyperclip.paste()
        except Exception:
            context["clipboard"] = ""

        def replace_var(match):
            key = match.group(1)
            offset = match.group(2)
            if offset:
                try:
                    delta = int(offset)
                except ValueError:
                    return match.group(0)
                now = datetime.now()
                if key == "date":
                    return (now + timedelta(days=delta)).strftime(
                        self.config.get("date_format", "%Y-%m-%d"))
                if key == "iso":
                    return (now + timedelta(days=delta)).isoformat(timespec="seconds")
                if key == "time":
                    return (now + timedelta(hours=delta)).strftime(
                        self.config.get("time_format", "%H:%M"))
                if key == "week":
                    return (now + timedelta(weeks=delta)).strftime("%V")
                return match.group(0)
            if key not in self.BUILTIN_PLACEHOLDERS and prompt_values and key in prompt_values:
                return str(prompt_values[key])
            return context.get(key, f"{{{key}}}")

        return re.sub(
            r"\{([A-Za-z_][A-Za-z0-9_]*)([+-]\d+)?\}", replace_var, text
        )

    def _capture_active_window(self):
        """X11 window id active right now, or None.

        Must be called BEFORE any Textr dialog opens: once the picker
        or prompt dialog appears, the active window is Textr's, not the
        target application's. xdotool is optional; without it, focus
        restore falls back to lowering the manager + a delay (see UI).
        Wayland has no xdotool active-window concept, so this is None.
        """
        try:
            if self.wayland:
                return None
            result = subprocess.run(
                ["xdotool", "getactivewindow"],
                capture_output=True, text=True, timeout=2,
            )
            wid = result.stdout.strip()
            return wid if wid.isdigit() else None
        except Exception:
            return None

    def _xdotool_available(self):
        """True when xdotool is installed (needed for exact focus restore)."""
        return shutil.which("xdotool") is not None

    def _restore_active_window(self, wid):
        """Activate window `wid` (xdotool); True on success, else False."""
        if not wid:
            return False
        try:
            subprocess.run(
                ["xdotool", "windowactivate", "--sync", wid],
                capture_output=True, timeout=3,
            )
            return True
        except Exception:
            return False

    def render_with_prompt(self, text):
        """Render `text`, prompting for missing values when a UI is attached.

        Returns the rendered text, or None when the user cancels the
        prompt (the caller must then abort the expansion, not insert a
        half-filled snippet). With no prompt_handler, behaves exactly like
        render_template (missing placeholders stay literal).
        """
        missing = self.missing_placeholders(text)
        if not missing or not self.prompt_handler:
            return self.render_template(text)
        # Capture the target window BEFORE the prompt dialog opens.
        self._prompt_target_window = self._capture_active_window()
        try:
            values = self.prompt_handler(missing)
        except Exception as e:
            print(f"Prompt failed: {e}")
            return self.render_template(text)
        if values is None:
            return None
        return self.render_template(text, prompt_values=values)

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
            return self.render_with_prompt(item.get("value", ""))

        if item_type == "template":
            raw = item.get("languages", {}).get(self.current_language)
            if not raw:
                return None
            return self.render_with_prompt(raw)

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

    def _start_expansion_worker(self):
        """Start the thread that performs expansions.

        replace() and _type_humanlike() sleep for a second or more while
        injecting output. Running those sleeps on the keyboard listener
        thread blocks the input backend (pynput's XRecord event loop, the
        evdev readers), which is what makes listeners die after a while; the
        worker keeps the listener free to keep reading keys.
        """
        if self._expansion_thread is not None:
            return
        self._expansion_thread = threading.Thread(
            target=self._expansion_worker, daemon=True
        )
        self._expansion_thread.start()

    def _expansion_worker(self):
        """Expansion worker body: pulls triggers off the queue and expands them.

        Queue items are either a trigger string (normal expansion) or a
        (None, text) tuple from insert_text() (quick picker: insert text
        without deleting a trigger first).
        """
        while self.running:
            try:
                item = self._expansion_queue.get(timeout=0.5)
            except queue.Empty:
                continue
            if item is None:
                break
            trigger, insert_text = item if isinstance(item, tuple) else (item, None)
            if trigger is None and insert_text is None:
                break
            try:
                if insert_text is not None:
                    self.replace("", insert_text)
                else:
                    self._expand(trigger)
            except Exception:
                print("ERROR: exception in expansion worker:")
                traceback.print_exc()
            finally:
                self.is_expanding = False

    def _schedule_expand(self, trigger):
        """Expand `trigger`, delegating to the worker thread when it is running.

        is_expanding is set synchronously so keys - including the ones the
        daemon injects while outputting - are ignored from this moment until
        the worker finishes, exactly like inline expansion, but without
        blocking the listener thread for the duration of the paste/typing.
        """
        if self._expansion_thread is None or not self._expansion_thread.is_alive():
            return self._expand(trigger)
        self.is_expanding = True
        self._expansion_queue.put(trigger)
        return True

    def _fire_pending(self):
        """Expand the deferred trigger (a boundary key or char completed it)."""
        trigger = self.pending_trigger
        self.pending_trigger = None
        if trigger is not None:
            self._schedule_expand(trigger)

    def _handle_key(self, key):
        if self.debugging and self.debug:
            print(f"DEBUG: Key pressed: {key}, Buffer: '{self.buffer}'")

        if self.is_expanding:
            return True

        # Ignore keys typed into the manager itself (add/edit dialogs, search
        # box...): they must never expand, or the user's own editing would
        # fire triggers and control commands. Keep the buffer clean so a
        # half-typed word doesn't survive a focus switch.
        if self.ui_focused:
            self.buffer = ""
            self.pending_trigger = None
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
            return self._schedule_expand(completed)

        if self.pending_trigger is not None:
            extra = self.buffer[self.pending_buffer_len:]
            if not self._pending_extends(extra):
                # e.g. ':d' followed by 'a' when only ':dt' could continue:
                # the deferred trigger is broken, not complete - drop it.
                self.pending_trigger = None

        return True

    def insert_text(self, trigger, text):
        """Insert `text` into the focused window (quick picker path).

        Unlike replace(), nothing is deleted first: the picker user has
        not typed a trigger. Records usage for `trigger` when given.
        Uses the expansion worker when it is running so the UI thread
        never blocks on the paste/typing sleeps.
        """
        if trigger:
            self._record_usage(trigger)
        if self._expansion_thread is not None and self._expansion_thread.is_alive():
            self.is_expanding = True
            self._expansion_queue.put((None, text))
        else:
            self.replace("", text)

    def show_picker_via_hotkey(self):
        """Global Ctrl+Alt+Space: open the quick-insert picker."""
        print("Global hotkey triggered: showing quick-insert picker")
        # Capture BEFORE the picker dialog opens, or the active window
        # recorded would be Textr's own dialog.
        self._picker_target_window = self._capture_active_window()
        if hasattr(self, "ui") and self.ui:
            self.ui.daemon_command.emit("picker")

    def toggle_pause_via_hotkey(self):
        self.enabled = not self.enabled
        print("Global hotkey triggered: Texter enabled =", self.enabled)

    def show_window_via_hotkey(self):
        """Global Ctrl+Shift+Space: bring the manager window to the front."""
        print("Global hotkey triggered: showing manager window")
        if hasattr(self, "ui") and self.ui:
            self.ui.daemon_command.emit("ui")

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
            on_show_hotkey=self.show_window_via_hotkey,
            layout=self.config.get("layout", "us"),
        )
        return self.wayland_input.start()

    def run(self):
        # Expansion work happens on its own thread so the input backends are
        # never blocked by the long paste/typing sleeps (see
        # _start_expansion_worker).
        self._start_expansion_worker()

        if self.wayland:
            print("Wayland session detected: capturing input via evdev and")
            print("typing output via ydotool (pynput is X11-only).")
            self._ydotool_available()
            if self._start_wayland_input():
                print("Texter daemon running (Wayland + evdev mode)")
                while self.running:
                    time.sleep(1)
                    # Watchdog: a reader thread can die silently (device
                    # error, exception in the read loop); respawn it so
                    # trigger detection keeps working.
                    if self.wayland_input and not self.wayland_input.healthy():
                        self.wayland_input.respawn()
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
                '<ctrl>+<shift>+p': self.toggle_pause_via_hotkey,
                '<ctrl>+<shift>+<space>': self.show_window_via_hotkey,
                '<ctrl>+<alt>+<space>': self.show_picker_via_hotkey,
            })
            self.hotkey_listener.daemon = True
            self.hotkey_listener.start()
            print("Global hotkeys registered: Ctrl+Shift+P to toggle pause/resume, "
                  "Ctrl+Shift+Space to show the manager, "
                  "Ctrl+Alt+Space for the quick-insert picker")
        except Exception as e:
            print("Failed to register global hotkeys listener:", e)

        # Keep trigger detection alive. pynput terminates its thread silently
        # for most internal failures (XRecord context disabled by the server,
        # X connection lost after lock/suspend, event-buffer overruns), and
        # then join() returns WITHOUT raising - so restart whenever join()
        # returns while the daemon still wants to run, not just when it
        # raises. A fresh Listener is required each time: a stopped one can
        # never be restarted.
        while self.running:
            self.listener = keyboard.Listener(on_press=self.on_press)
            self.listener.daemon = True
            self.listener.start()
            try:
                self.listener.join()
            except Exception:
                # join() re-raises when a callback crashed (on_press already
                # guards against that); treat it as a crash too.
                print("Keyboard listener crashed, restarting:")
                traceback.print_exc()
            else:
                # join() returned cleanly: either stop() was requested or the
                # listener died silently. Only the former should end the loop.
                if self.running:
                    print("Keyboard listener stopped unexpectedly; restarting "
                          "so trigger detection keeps working")
            if not self.running:
                break
            time.sleep(0.5)

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

    
