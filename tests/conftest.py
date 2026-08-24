import json
import os
import sys
import threading
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import daemon as daemon_mod
from pynput import keyboard


class FakeClipboard:
    def __init__(self):
        self.text = ""

    def paste(self):
        return self.text

    def copy(self, text):
        self.text = text


class FakeController:
    """Records press/release calls; safe to instantiate without X11."""

    def __init__(self):
        self.events = []

    def press(self, key):
        self.events.append(("press", key))

    def release(self, key):
        self.events.append(("release", key))

    def backspaces(self):
        return [
            k for action, k in self.events
            if action == "press" and k == keyboard.Key.backspace
        ]


def type_string(d, text):
    for ch in text:
        d.on_press(SimpleNamespace(char=ch))


@pytest.fixture
def d(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    # Keep all data files inside the test dir, never the real ~/.config.
    monkeypatch.setenv("TEXTR_CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(daemon_mod.keyboard, "Controller", FakeController)
    monkeypatch.setattr(daemon_mod, "pyperclip", FakeClipboard())
    monkeypatch.setattr(daemon_mod.time, "sleep", lambda seconds: None)
    snippets = {
        "sig": {"type": "variable", "value": "Alvaro <email>", "category": "test"},
        "longtrig": {"type": "template", "languages": {"python": "print(1)"}, "category": "test"},
        ":d": {"type": "command", "action": "date", "category": "test"},
    }
    (tmp_path / "snippets.json").write_text(json.dumps(snippets))
    return daemon_mod.Daemon(snippets_file="snippets.json")
