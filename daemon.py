# texter_mvp/daemon.py
from datetime import datetime
import time
import re
import pyperclip
from pynput import keyboard


class Daemon:
    def __init__(self):
        self.buffer = ""
        self.max_buffer = 100
        self.running = True
        self.enabled = True
        self.is_expanding = False
        self.current_language = "python"

        self.controller = keyboard.Controller()

        self.snippets = {
            ":pause": {"type": "command", "action": "toggle_pause"},
            ":resume": {"type": "command", "action": "resume"},
            ":quit": {"type": "command", "action": "shutdown"},
            ":ex": {"type": "command", "action": "shutdown"},

            "func": {
                "type": "template",
                "languages": {
                    "python": "def my_function():\n    pass",
                    "javascript": "function myFunction() {}"
                }
            },
            "cvenv": {
                "type": "template",
                "languages": {
                    "python": "python3 -m venv .venv",
                }
            },
            "avenv": {
                "type": "template",
                "languages": {
                    "python": "source .venv/bin/activate",
                }
            },

            "mn": {"type": "variable", "value": "Alvaro"},
            "mln": {"type": "variable", "value": "de Landaluce"},
            "mfn": {"type": "variable", "value": "Alvaro de Landaluce"},
            "mgm": {"type": "variable", "value": "alvarolandaluce@gmail.com"},

            "wn": {"type": "variable", "value": "Bruna"},
            "wmn": {"type": "variable", "value": "Karoline"},
            "wln": {"type": "variable", "value": "Tatematsu"},
            "wgm": {"type": "variable", "value": "brunatatematsu@gmail.com"},

            "date": {"type": "dynamic", "function": lambda: time.strftime("%Y-%m-%d")},
            "slp": {"type": "command", "action": "set_language", "value": "python"},
            "slj": {"type": "command", "action": "set_language", "value": "javascript"},
            "fsig": {"type": "variable", "value": "Best Regards,\nAlvaro"},
            "sig": {"type": "variable", "value": "Best,\nAlvaro"},
        }

    def run_command(self, item):
        action = item.get("action")

        if action == "set_language":
            self.current_language = item.get("value")
            print("Language switched to:", self.current_language)

        elif action == "shutdown":
            print("Shutting down Texter daemon...")
            self.running = False

        elif action == "toggle_pause":
            self.enabled = not self.enabled
            print("Texter enabled =", self.enabled)

        elif action == "resume":
            self.enabled = True
            print("Texter resumed")

    def get_context(self):
        return {
            "name": "Alvaro",
            "email": "alvaro@example.com",
            "date": datetime.now().strftime("%Y-%m-%d"),
            "time": datetime.now().strftime("%H:%M"),
        }

    def render_template(self, text):
        context = self.get_context()

        def replace_var(match):
            key = match.group(1)
            return context.get(key, f"{{{key}}}")

        return re.sub(r"\{(\w+)\}", replace_var, text)

    def replace(self, trigger, expansion):
        try:
            self.is_expanding = True
            time.sleep(0.03)

            for _ in range(len(trigger)):
                self.controller.press(keyboard.Key.backspace)
                self.controller.release(keyboard.Key.backspace)
                time.sleep(0.015)

            time.sleep(0.02)
            pyperclip.copy(expansion)
            time.sleep(0.03)

            self.controller.press(keyboard.Key.ctrl)
            self.controller.press("v")
            self.controller.release("v")
            self.controller.release(keyboard.Key.ctrl)
            time.sleep(0.03)

        except Exception as e:
            print("REPLACE ERROR:", repr(e))

        finally:
            self.buffer = ""
            self.is_expanding = False

    def is_valid_trigger(self, trigger):
        item = self.snippets.get(trigger)

        if not item:
            return None

        if item["type"] == "command":
            self.run_command(item)
            for _ in range(len(trigger)):
                self.controller.press(keyboard.Key.backspace)
                self.controller.release(keyboard.Key.backspace)
            return None

        if item["type"] == "variable":
            return item["value"]

        if item["type"] == "template":
            raw = item["languages"].get(self.current_language)
            if not raw:
                return None
            return self.render_template(raw)

        return None

    def on_press(self, key):
        if self.is_expanding:
            return True

        if key == keyboard.Key.backspace:
            self.buffer = self.buffer[:-1]
            return True

        try:
            char = key.char
        except AttributeError:
            return True

        if not char:
            return True

        self.buffer += char
        self.buffer = self.buffer[-self.max_buffer:]

        # CHECK IF PAUSED - skip all expansion but still allow commands
        if not self.enabled:
            # print("Texter is PAUSED - ignoring triggers")
            self.buffer = ""
            return True

        for trigger in self.snippets:
            if not self.buffer.endswith(trigger):
                continue

            expansion = self.is_valid_trigger(trigger)

            if expansion is not None:
                self.replace(trigger, expansion)
                break

        return True

    def run(self):
        print("Texter daemon running (X11 mode)")
        print("Try typing 'mn' -> should expand to 'Alvaro'")
        print("Type ':pause' to pause, ':resume' to resume")

        with keyboard.Listener(on_press=self.on_press) as listener:
            listener.join()

    def toggle(self):
        """Toggle pause/resume from UI"""
        self.enabled = not self.enabled
        print("Texter enabled =", self.enabled)
        return self.enabled

    def stop(self):
        self.running = False


if __name__ == "__main__":
    Daemon().run()