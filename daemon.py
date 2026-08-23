from datetime import datetime
import time
import re
import pyperclip
import json
import os
import random
import math
import string
import threading
import traceback
from pynput import keyboard


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
        self.current_language = "python"
        self.listener = None
        self.hotkey_listener = None
        self.controller = keyboard.Controller()
        self.snippets_file = snippets_file
        self.snippets = {}
        # Guards all access to self.snippets: the UI thread mutates it while
        # the keyboard listener iterates it on every keystroke.
        self.snippets_lock = threading.RLock()
        # Keys that invalidate the typed buffer (cursor jumps, line edits...)
        # Compared by identity: pynput key objects are not reliably hashable.
        self.buffer_reset_keys = (
            keyboard.Key.enter, keyboard.Key.tab, keyboard.Key.esc,
            keyboard.Key.delete, keyboard.Key.home, keyboard.Key.end,
            keyboard.Key.page_up, keyboard.Key.page_down,
            keyboard.Key.up, keyboard.Key.down,
            keyboard.Key.left, keyboard.Key.right,
        )
        self.load_snippets()
        self.load_config()

    def load_config(self):
        self.config_file = "config.json"
        self.config = {"name": "Alvaro", "email": "alvaro@example.com"}
        if os.path.exists(self.config_file):
            try:
                with open(self.config_file, "r") as f:
                    self.config.update(json.load(f))
                print("Loaded user settings from config.json")
            except Exception as e:
                print("Failed to load config:", e)

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

    def run_command(self, item):
        action = item.get("action")

        if action == "set_language":
            self.current_language = item.get("value")
            print("Language switched to:", self.current_language)

        elif action == "shutdown":
            print("Shutting down Texter daemon...")
            self.running = False
            self.save_snippets()
            if self.listener:
                self.listener.stop()
            
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

        elif action == "date":
            if hasattr(self, "ui") and self.ui:
                self.ui.daemon_command.emit("insert_date")

        elif action == "time":
            if hasattr(self, "ui") and self.ui:
                self.ui.daemon_command.emit("insert_time")

        elif action == "datetime":
            if hasattr(self, "ui") and self.ui:
                self.ui.daemon_command.emit("insert_datetime")

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
        if self.listener:
            self.listener.stop()
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
            "date": datetime.now().strftime("%Y-%m-%d"),
            "time": datetime.now().strftime("%H:%M"),
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
        old_clipboard = ""
        try:
            old_clipboard = pyperclip.paste()
        except Exception:
            pass

        try:
            self.is_expanding = True
            time.sleep(0.03)

            # Delete the trigger with human-like backspace timing
            for _ in range(len(trigger)):
                self.controller.press(keyboard.Key.backspace)
                self.controller.release(keyboard.Key.backspace)
                time.sleep(random.uniform(0.04, 0.12))  # variable backspace speed

            time.sleep(random.uniform(0.08, 0.25))  # natural pause before typing

            if self.writing_mode:
                self._type_humanlike(expansion)
            else:
                # Paste mode with jitter
                pyperclip.copy(expansion)
                time.sleep(random.uniform(0.05, 0.09))
                self.controller.press(keyboard.Key.shift)
                self.controller.press(keyboard.Key.ctrl)
                self.controller.press("v")
                self.controller.release("v")
                self.controller.release(keyboard.Key.ctrl)
                self.controller.release(keyboard.Key.shift)
                time.sleep(random.uniform(0.3, 0.8))

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
            self.run_command(item)
            for _ in range(len(trigger)):
                self.controller.press(keyboard.Key.backspace)
                self.controller.release(keyboard.Key.backspace)
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
        # Never let an exception escape into the pynput listener thread:
        # that would silently kill the listener and stop all trigger detection.
        try:
            return self._handle_key(key)
        except Exception:
            print("ERROR: exception in key handler (listener kept alive):")
            traceback.print_exc()
            return True

    def _handle_key(self, key):
        if self.debugging and self.debug:
            print(f"DEBUG: Key pressed: {key}, Buffer: '{self.buffer}'")   

        if self.is_expanding:
            return True

        if key == keyboard.Key.backspace:
            self.buffer = self.buffer[:-1]
            return True

        if any(key is reset_key for reset_key in self.buffer_reset_keys):
            self.buffer = ""
            return True

        try:
            char = key.char
        except AttributeError:
            return True

        if not char:
            return True

        self.buffer += char
        self.buffer = self.buffer[-self.max_buffer:]

        with self.snippets_lock:
            sorted_triggers = sorted(self.snippets.keys(), key=len, reverse=True)

        def is_trigger_complete(buffer, trigger):
            if not buffer.endswith(trigger):
                return False
            # Check that the character before the trigger is a word boundary
            if len(buffer) == len(trigger):
                return True  # Just typed the trigger
            preceding_char = buffer[-(len(trigger) + 1)]
            return preceding_char in ' \t\n.,;:!?"\'()-_[]{}<>/\\'

        # Check for triggers BEFORE checking if paused
        # This allows commands like :resume and :quit to work even when paused
        for trigger in sorted_triggers:
            if is_trigger_complete(self.buffer, trigger):
                with self.snippets_lock:
                    item = self.snippets.get(trigger)

                if item is None:
                    return True

                # Always allow commands to work (even when paused)
                if item.get("type") == "command":
                    expansion = self.is_valid_trigger(trigger)
                    if expansion is not None:
                        self.replace(trigger, expansion)
                    return True
                
                # For non-commands, only expand if not paused
                if not self.enabled:
                    # Clear buffer and ignore when paused
                    self.buffer = ""
                    return True
                
                # Normal expansion when enabled
                expansion = self.is_valid_trigger(trigger)
                if expansion is not None:
                    self.replace(trigger, expansion)
                return True

        return True

    def toggle_pause_via_hotkey(self):
        self.enabled = not self.enabled
        print("Global hotkey triggered: Texter enabled =", self.enabled)

    def run(self):
        print("Texter daemon running (X11 mode)")
        
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

    def stop(self):
        self.running = False
        self.save_snippets()  # Save before stopping
        if self.listener:
            self.listener.stop()
        if self.hotkey_listener:
            try:
                self.hotkey_listener.stop()
            except Exception:
                pass

    
