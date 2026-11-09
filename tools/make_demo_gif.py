#!/usr/bin/env python3
"""Render the README demo GIF (assets/demo.gif) for Textr.

The text that appears in the animation is produced by the real expansion
engine: this script builds a Daemon against a throwaway config directory and
feeds normalized keys through ``Daemon._handle_key``, exactly like the X11 and
Wayland input backends do. Only the final insertion (``Daemon.replace``) is
intercepted, and the result is painted into an editor window drawn here.

It is hermetic: no keyboard hook is installed, pynput's X11 ``Controller`` is
stubbed out, Qt runs on the offscreen platform and DISPLAY is unset, so it is
safe to run while a real Textr instance is already running.

Usage:
    python tools/make_demo_gif.py [-o assets/demo.gif]
    python tools/make_demo_gif.py --contact-sheet /tmp/sheet.png

Requires PySide6 (already a dependency) and ffmpeg.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

# --- hermetic environment: must happen before daemon / Qt are imported ------
os.environ["QT_QPA_PLATFORM"] = "offscreen"
for _var in ("DISPLAY", "WAYLAND_DISPLAY", "XDG_SESSION_TYPE"):
    os.environ.pop(_var, None)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import types


def _install_stubs():
    """Replace pynput and pyperclip with inert stand-ins.

    pynput's backend refuses to import without an X connection, and its
    controller would inject real keystrokes; pyperclip would read the user's
    clipboard. The demo needs neither: it feeds ``keys.Key`` objects straight
    into the daemon and paints the result itself.
    """

    class FakeKey:
        __slots__ = ("name",)

        def __init__(self, name):
            self.name = name

        def __repr__(self):
            return f"<Key.{self.name}>"

    class KeyNamespace:
        def __getattr__(self, item):
            return FakeKey(item)

    class NullController:
        def __init__(self, *args, **kwargs):
            pass

        def press(self, *args, **kwargs):
            pass

        def release(self, *args, **kwargs):
            pass

        def type(self, *args, **kwargs):
            pass

    class NullKeyCode:
        def __init__(self, *args, **kwargs):
            pass

        @classmethod
        def from_char(cls, char):
            return char

        @classmethod
        def from_vk(cls, vk, **kwargs):
            return None

    class NullListener:
        def __init__(self, *args, **kwargs):
            pass

        def start(self):
            pass

        def stop(self):
            pass

        def join(self, *args, **kwargs):
            pass

        def run(self):
            pass

    key_mod = types.ModuleType("pynput.keyboard")
    key_mod.Key = KeyNamespace()
    key_mod.Controller = NullController
    key_mod.KeyCode = NullKeyCode
    key_mod.Listener = NullListener
    key_mod.GlobalHotKeys = NullListener
    key_mod.HotKey = NullListener

    pynput_mod = types.ModuleType("pynput")
    pynput_mod.keyboard = key_mod
    pynput_mod.mouse = types.ModuleType("pynput.mouse")

    clipboard_mod = types.ModuleType("pyperclip")
    clipboard_mod.paste = lambda: ""
    clipboard_mod.copy = lambda text: None

    sys.modules["pynput"] = pynput_mod
    sys.modules["pynput.keyboard"] = key_mod
    sys.modules["pynput.mouse"] = pynput_mod.mouse
    sys.modules["pyperclip"] = clipboard_mod


_install_stubs()

from PySide6.QtCore import QPointF, QRectF, Qt  # noqa: E402
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QImage, QPainter, QPen  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import daemon as daemon_module  # noqa: E402
from keys import Key  # noqa: E402

# --- look -------------------------------------------------------------------
MANTLE = QColor("#11111b")
BASE = QColor("#1e1e2e")
BORDER = QColor("#313244")
TEXT = QColor("#cdd6f4")
SUBTLE = QColor("#6c7086")
FAINT = QColor("#45475a")
MAUVE = QColor("#cba6f7")
BLUE = QColor("#89b4fa")
GREEN = QColor("#a6e3a1")
YELLOW = QColor("#f9e2af")
RED = QColor("#f38ba8")

W, H = 880, 420
MARGIN = 14
TITLE_H = 42
LINE_H = 27
FONT_PX = 17
TEXT_X = MARGIN + 66
TEXT_TOP = MARGIN + TITLE_H + 18
FPS = 15

# --- demo content -----------------------------------------------------------
# A throwaway profile: never the real user's name, email or snippets.
DEMO_CONFIG = {
    "name": "Alex Rivera",
    "email": "alex@example.com",
    "layout": "us",
    "language": "python",
    "paste_hotkey": "ctrl+shift+v",
    "date_format": "%Y-%m-%d",
    "time_format": "%H:%M",
    "user_settings": {},
}

PY_TEMPLATE = (
    "def handle_request(request):\n"
    '    """Validate and store{cursor} the request."""\n'
    "    return None"
)
TRY_TEMPLATE = (
    "try:\n"
    "    {cursor}\n"
    "except Exception as exc:\n"
    "    log.error(exc)"
)

DEMO_SNIPPETS = {
    ":py": {
        "type": "template",
        "category": "python",
        "tags": ["python"],
        "value": PY_TEMPLATE,
        "languages": {"python": PY_TEMPLATE},
        "usage": 0,
    },
    ":try": {
        "type": "template",
        "category": "python",
        "tags": ["python"],
        "value": TRY_TEMPLATE,
        "languages": {"python": TRY_TEMPLATE},
        "usage": 0,
    },
}

START_LINES = [
    "import logging",
    "",
    "log = logging.getLogger(__name__)",
    "",
]

# --- pack demos -------------------------------------------------------------
# One GIF per Gumroad pack. Triggers and message bodies come from the real pack
# files (python/, job/, recruiter/ in SnippetPacks); placeholders are pre-filled
# so expansion does not stop to prompt while the frames are captured.

def _snip(value):
    return {"type": "variable", "category": "demo", "tags": [], "value": value, "usage": 0}


PY_PYTEST = "\n".join([
    'def test_health_check():',
    '    result = client.get("/health")',
    '    assert result == 200',
])

PY_CLASS = "\n".join([
    'class RateLimiter:',
    '    def __init__(self, limit: int):',
    '        self.limit = limit',
    '',
    '    def allow(self) -> bool:',
    '        return True',
])

JOB_BUMP = "\n".join([
    'Hi Sarah, bumping this in case it got buried — I applied for the',
    'Product Analyst role at Northwind on 3 March and remain keen. If',
    'the role has been filled or the timing has shifted, no worries',
    "at all — just let me know and I'll close the loop on my end.",
    'Thanks, Alex',
])

JOB_STATUS = "\n".join([
    'Thanks again for talking about the Product Analyst role at',
    'Northwind. I wanted to check in on next steps and timing — is',
    'there anything else you need from me at this stage? I am excited',
    'about the role and happy to work around your schedule.',
])

REC_REMINDER = "\n".join([
    'Hi Priya, quick reminder that your technical screen with Northwind',
    'is tomorrow at 10:00 with Dana and Miguel. Video call, link in the',
    'invite. Let me know if anything comes up — otherwise, good luck!',
    'Alex',
])

REC_COLD = "\n".join([
    'Hi Priya, I came across your profile and your work on backend',
    "infrastructure at Meridian really stood out. I'm Alex, a recruiter",
    'at Northwind, hiring for a Senior Backend Engineer — London/remote',
    '— on the Platform team, building the ingestion layer. The range is',
    '95-115k. Open to a quick 15-minute chat this week?',
])
# --- demo-data privacy guard -------------------------------------------------
# Everything these scenarios show is invented, so none of it may carry a real
# person's details. The owner's surname once leaked into a mail signature, so any
# scenario, snippet, header or window title containing one of these tokens
# (case-insensitive) refuses to render instead of shipping the GIF.
PERSONAL_IDENTIFIERS = ("landaluce", "alvaro")


def _iter_text(value):
    """Yield every string inside a scenario's nested dicts, lists and tuples."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _iter_text(item)
    elif isinstance(value, (list, tuple, set)):
        for item in value:
            yield from _iter_text(item)


def check_personal_data(name, payload):
    """Abort the render if demo data contains a real identifier."""
    hits = []
    for text in _iter_text(payload):
        lowered = text.lower()
        for token in PERSONAL_IDENTIFIERS:
            if token in lowered:
                hits.append((token, text))
                break
    if hits:
        report = "\n".join(
            "  - " + token + " in: " + text.replace("\n", " / ")[:90]
            for token, text in hits
        )
        sys.exit(
            "refusing to render '" + name + "': demo data contains a real personal "
            "detail. Use an invented placeholder instead:\n" + report
        )


PACK_SCENARIOS = {
    "python": {
        "title": "test_api.py",
        "syntax": True,
        "start": ["from fastapi.testclient import TestClient", ""],
        "snippets": {";py-pytest": _snip(PY_PYTEST), ";py-class": _snip(PY_CLASS)},
        "steps": [
            ("hold", 10),
            ("type", ";py-pytest"),
            ("snap", None),
            ("hold", 18),
            ("newline", None),
            ("type", ";py-class"),
            ("snap", None),
            ("hold", 26),
        ],
    },
    "job": {
        "title": "New message",
        "chrome": "email",
        "header": [
            ("To", "sarah.chen@northwind.com"),
            ("Subject", "Following up — Product Analyst application"),
        ],
        "font_px": 15,
        "line_h": 22,
        "syntax": False,
        # Start empty: the pack snippets themselves open with "Hi Sarah,".
        "start": [""],
        "snippets": {";job-bump": _snip(JOB_BUMP), ";job-status": _snip(JOB_STATUS)},
        "steps": [
            ("hold", 10),
            ("type", ";job-bump"),
            ("snap", None),
            ("hold", 20),
            ("newline", None),
            ("type", ";job-status"),
            ("snap", None),
            ("hold", 28),
        ],
    },
    "recruiter": {
        "title": "New message",
        "chrome": "email",
        "header": [
            ("To", "priya.raman@meridian.dev"),
            ("Subject", "Senior Backend Engineer — quick intro"),
        ],
        "font_px": 15,
        "line_h": 22,
        "syntax": False,
        # Start empty: the pack snippets themselves open with "Hi Priya,".
        "start": [""],
        "snippets": {";rec-reminder": _snip(REC_REMINDER), ";rec-cold": _snip(REC_COLD)},
        "steps": [
            ("hold", 10),
            ("type", ";rec-reminder"),
            ("snap", None),
            ("hold", 20),
            ("newline", None),
            ("type", ";rec-cold"),
            ("snap", None),
            ("hold", 28),
        ],
    },
    # --- code packs render in an editor --------------------------------------
    "sql": {
        "title": "quarterly.sql",
        "syntax": True,
        "language": "sql",
        "font_px": 14,
        "line_h": 21,
        "start": ["-- Revenue trends for the 2026 fiscal year", ""],
        "snippets": {
            ";sql-window": _snip("\n".join([
                "SELECT region, month, revenue,",
                "  SUM(revenue) OVER (PARTITION BY region ORDER BY month) AS running_total",
                "FROM monthly_revenue",
                "WHERE fiscal_year = 2026;",
            ])),
            ";sql-group": _snip("\n".join([
                "SELECT region, COUNT(*) AS orders, SUM(total) AS revenue",
                "FROM orders",
                "WHERE status = 'paid'",
                "GROUP BY region",
                "HAVING SUM(total) > 100000",
                "ORDER BY revenue DESC;",
            ])),
        },
        "steps": [
            ("hold", 10),
            ("type", ";sql-window"),
            ("snap", None),
            ("hold", 18),
            ("newline", None),
            ("type", ";sql-group"),
            ("snap", None),
            ("hold", 26),
        ],
    },
    "javascript": {
        "title": "cart.js",
        "syntax": True,
        "language": "js",
        "font_px": 13,
        "line_h": 19,
        "start": [""],
        "snippets": {
            ";js-fetch": _snip("\n".join([
                "async function loadOrders() {",
                "  const response = await fetch('/api/orders', {credentials: 'include'});",
                "  if (!response.ok) {",
                "    throw new Error('Could not load orders');",
                "  }",
                "  const data = await response.json();",
                "  return data;",
                "}",
            ])),
            ";js-class": _snip("\n".join([
                "class Cart {",
                "  constructor(items) {",
                "    this.items = items;",
                "  }",
                "",
                "  total() {",
                "    return this.items.reduce((sum, item) => sum + item.price, 0);",
                "  }",
                "}",
            ])),
        },
        "steps": [
            ("hold", 10),
            ("type", ";js-fetch"),
            ("snap", None),
            ("hold", 18),
            ("newline", None),
            ("type", ";js-class"),
            ("snap", None),
            ("hold", 26),
        ],
    },
    # --- writing packs render in the app they are written in ------------------
    "workflow": {
        "title": "northgate/notes · New pull request",
        "syntax": False,
        "chrome": "pr",
        "header": [("Title", "Fix duplicate trigger firing on prefix disambiguation")],
        "font_px": 13,
        "line_h": 18,
        "start": [""],
        "snippets": {
            ";dev-pr": _snip("\n".join([
                "## Summary",
                "Fix :d firing before :dt: the daemon now defers a short trigger",
                "while it is a prefix of another.",
                "",
                "## Changes",
                "- Track the pending trigger in the key handler",
                "- Fire on boundary keys, abort on mismatch",
                "- Cover both paths in the prefix tests",
                "",
                "## Testing",
                "pytest tests/test_prefix.py — 14 passed.",
                "",
                "## Risks",
                "Small: disabled snippets use the same deferral path.",
                "",
                "Closes #412. Please review when you can.",
            ])),
        },
        "steps": [
            ("hold", 10),
            ("type", ";dev-pr"),
            ("snap", None),
            ("hold", 34),
        ],
    },
    "sales": {
        "title": "New message",
        "chrome": "email",
        "header": [
            ("To", "marcus.hale@acmesupply.com"),
            ("Subject", "Re: order notes for Acme Supply"),
        ],
        "font_px": 15,
        "line_h": 22,
        "syntax": False,
        "start": ["Hi Marcus,", ""],
        "snippets": {
            ";sales-demo": _snip("\n".join([
                "thanks for your interest in Textr. I'd love to show you a 20-minute",
                "demo focused on order-note snippets, with time for questions at the",
                "end. I'm free Tuesday or Thursday afternoon — does either work?",
            ])),
            ";sales-bump": _snip("\n".join([
                "bumping this in case it got buried. The offer stands: a 15-minute",
                "look at how Textr handles repeat order notes for Acme Supply. If the",
                "timing is wrong, no worries — just let me know.",
            ])),
        },
        "steps": [
            ("hold", 10),
            ("type", ";sales-demo"),
            ("snap", None),
            ("hold", 18),
            ("newline", None),
            ("type", ";sales-bump"),
            ("snap", None),
            ("hold", 26),
        ],
    },
    "real-estate": {
        "title": "New message",
        "chrome": "email",
        "header": [
            ("To", "jordan.hale@gmail.com"),
            ("Subject", "Showing Saturday — 24 Alder Lane"),
        ],
        "font_px": 15,
        "line_h": 22,
        "syntax": False,
        "start": ["Hi Jordan,", ""],
        "snippets": {
            ";re-showing": _snip("\n".join([
                "Great — I'd love to show you 24 Alder Lane. How about Saturday at",
                "10:30? We'll meet at the front porch and plan for about 30 minutes.",
                "Let me know if that works, or send a couple of alternatives.",
            ])),
            ";re-followup-showing": _snip("\n".join([
                "Thank you for seeing Alder Lane today. What did you think — especially",
                "the kitchen? If you'd like a second look or more photos, just say so.",
                "And if it's not the one, tell me what was missing and I'll send other",
                "listings that fit better.",
            ])),
        },
        "steps": [
            ("hold", 10),
            ("type", ";re-showing"),
            ("snap", None),
            ("hold", 18),
            ("newline", None),
            ("type", ";re-followup-showing"),
            ("snap", None),
            ("hold", 26),
        ],
    },
    "freelancer": {
        "title": "New message",
        "chrome": "email",
        "header": [
            ("To", "maya@brightwave.co"),
            ("Subject", "Project update — checkout redesign"),
        ],
        "font_px": 15,
        "line_h": 22,
        "syntax": False,
        "start": ["Hi Maya,", ""],
        "snippets": {
            ";client-update": _snip("\n".join([
                "an update on the checkout redesign:",
                "Done: cart and payment screens",
                "In progress: order confirmation",
                "Next: review build by Friday",
                "Blockers: none",
            ])),
            ";client-invoice": _snip("\n".join([
                "attached is invoice INV-2041 for the checkout redesign: $4,800, due",
                "30 November. Payment details are on the invoice. Thank you for your",
                "business — let me know if you have any questions about it.",
            ])),
        },
        "steps": [
            ("hold", 10),
            ("type", ";client-update"),
            ("snap", None),
            ("hold", 18),
            ("newline", None),
            ("type", ";client-invoice"),
            ("snap", None),
            ("hold", 26),
        ],
    },
    "customer-support": {
        "title": "Ticket #4821 · Reply",
        "syntax": False,
        "chrome": "ticket",
        "header": [
            ("From", "Dana Whitfield <dana@acme.io>"),
            ("Subject", "Order 88213 arrived damaged"),
        ],
        "font_px": 15,
        "line_h": 22,
        "start": ["Hi Dana,", ""],
        "snippets": {
            ";sup-acknowledge": _snip("\n".join([
                "thanks for flagging this. I understand the damaged box is affecting",
                "your install date, and I'm sorry for the trouble. I've got your case",
                "and I'll look into it now — I'll update you by Thursday at the latest.",
            ])),
            ";sup-workaround": _snip("\n".join([
                "while the warehouse investigates, here's a workaround that should",
                "help in the meantime: use the spare bracket from the parts bag and",
                "skip the damaged rail. I'll update you by Thursday with the fix.",
            ])),
        },
        "steps": [
            ("hold", 10),
            ("type", ";sup-acknowledge"),
            ("snap", None),
            ("hold", 18),
            ("newline", None),
            ("type", ";sup-workaround"),
            ("snap", None),
            ("hold", 26),
        ],
    },
    # --- site demos: one per landing page, each showing that page's promise ---
    "espanso": {
        "mode": "import",
        "title": "Textr — Snippets",
        "syntax": False,
        "start": [""],
        "snippets": {},
        "steps": None,
        "banner_reading": "Reading espanso.yml…",
        "banner_done": "Imported 8 matches from espanso.yml",
        "rows": [
            (":greet", "Hello, how are you?"),
            (":sig", "Alex Rivera — Software Engineer"),
            (":email", "alex@example.com"),
            (":tel", "+44 7700 900123"),
            (":addr", "Unit 4, 22 Alder Lane, Manchester M4 5JX"),
            (":ty", "Thank you for your time,"),
            (":br", "Best regards,"),
            (":now", "2026-11-09 14:32"),
        ],
    },
    "wayland": {
        "title": "alex@nixos: ~/projects/textr",
        "chrome": "terminal",
        "syntax": False,
        "font_px": 15,
        "line_h": 24,
        "start": [
            "$ git status",
            "On branch master",
            "Your branch is up to date with 'origin/master'.",
            "",
            "nothing to commit, working tree clean",
            "$ ",
        ],
        "caret_col": 2,
        "prompt": "$ ",
        "snippets": {
            ":gd": _snip("git diff --stat HEAD~1"),
            ":gl": _snip("git log --oneline -4"),
        },
        "steps": [
            ("hold", 12),
            ("type", ":gd"),
            ("snap", None),
            ("hold", 10),
            ("run", [
                " input_wayland.py | 24 ++++++++++++++--------",
                " keys.py          |  6 +++---",
                " 2 files changed, 21 insertions(+), 9 deletions(-)",
            ]),
            ("type", ":gl"),
            ("snap", None),
            ("hold", 10),
            ("run", [
                "4b7d3aa Capture modifier state from evdev",
                "a9f0c15 Defer a trigger while it is a prefix",
                "e1c9a2f Type expansions with ydotool on Wayland",
            ]),
            ("hold", 22),
        ],
    },
    "linux": {
        "title": "alex@nixos: ~/srv",
        "chrome": "terminal",
        "syntax": False,
        "font_px": 15,
        "line_h": 24,
        "start": [
            "$ systemctl --user is-active textr",
            "active",
            "$ ",
        ],
        "caret_col": 2,
        "prompt": "$ ",
        "snippets": {
            ":listen": _snip("ss -tulpn | grep LISTEN"),
            ":serve": _snip("python3 -m http.server 8000"),
        },
        "steps": [
            ("hold", 12),
            ("type", ":listen"),
            ("snap", None),
            ("hold", 10),
            ("run", [
                "LISTEN 0  128   0.0.0.0:22      0.0.0.0:*",
                "LISTEN 0  4096  127.0.0.1:631   0.0.0.0:*",
            ]),
            ("type", ":serve"),
            ("snap", None),
            ("hold", 10),
            ("run", [
                "Serving HTTP on 0.0.0.0 port 8000 (http://0.0.0.0:8000/) ...",
            ]),
            ("hold", 22),
        ],
    },
    "windows": {
        "title": "New message",
        "chrome": "email",
        "header": [
            ("To", "marcus.hale@acmesupply.com"),
            ("Subject", "Re: order notes for Acme Supply"),
        ],
        "font_px": 15,
        "line_h": 22,
        "syntax": False,
        "start": ["Hi Marcus,", ""],
        "snippets": {
            ":sig": _snip("Alex Rivera\nOperations — Northgate Systems\n+44 7700 900123"),
            ":addr": _snip(
                "Northgate Systems\nUnit 4, 22 Alder Lane\nManchester M4 5JX\nUnited Kingdom"
            ),
        },
        "steps": [
            ("hold", 10),
            ("type", ":sig"),
            ("snap", None),
            ("hold", 18),
            ("newline", None),
            ("type", ":addr"),
            ("snap", None),
            ("hold", 26),
        ],
    },
    "textexpander": {
        "title": "standup-2026-11-09.md",
        "syntax": False,
        "font_px": 15,
        "line_h": 22,
        "start": ["## Standup — 9 November", ""],
        "snippets": {
            ":mtg": _snip(
                "### Meeting notes\n- Decided:\n- Open questions:\n- Owner:"
            ),
            ":todo": _snip("- [ ] \n- [ ] "),
        },
        "steps": [
            ("hold", 10),
            ("type", ":mtg"),
            ("snap", None),
            ("hold", 20),
            ("newline", None),
            ("type", ":todo"),
            ("snap", None),
            ("hold", 26),
        ],
    },
}

DEFAULT_OUTPUTS = {
    "demo": os.path.join(ROOT, "assets", "demo.gif"),
    "python": os.path.join(ROOT, "assets", "pack-python.gif"),
    "job": os.path.join(ROOT, "assets", "pack-job-seeker.gif"),
    "recruiter": os.path.join(ROOT, "assets", "pack-recruiter.gif"),
    "sql": os.path.join(ROOT, "assets", "pack-sql.gif"),
    "javascript": os.path.join(ROOT, "assets", "pack-javascript.gif"),
    "workflow": os.path.join(ROOT, "assets", "pack-developer-workflow.gif"),
    "sales": os.path.join(ROOT, "assets", "pack-sales.gif"),
    "real-estate": os.path.join(ROOT, "assets", "pack-real-estate.gif"),
    "freelancer": os.path.join(ROOT, "assets", "pack-freelancer-client.gif"),
    "customer-support": os.path.join(ROOT, "assets", "pack-customer-support.gif"),
    "espanso": os.path.join(ROOT, "assets", "demo-espanso.gif"),
    "wayland": os.path.join(ROOT, "assets", "demo-wayland.gif"),
    "linux": os.path.join(ROOT, "assets", "demo-linux.gif"),
    "windows": os.path.join(ROOT, "assets", "demo-windows.gif"),
    "textexpander": os.path.join(ROOT, "assets", "demo-textexpander.gif"),
}

# Landing-page demos: rendered on demand, never as part of --all-packs.
SITE_DEMOS = {"espanso", "wayland", "linux", "windows", "textexpander"}


class Editor:
    """Minimal text model: just enough to place a caret and insert text."""

    def __init__(self, lines):
        self.lines = list(lines)
        self.line = len(lines) - 1
        self.col = 0

    def insert(self, text):
        if not text:
            return
        parts = text.split("\n")
        head = self.lines[self.line][: self.col]
        tail = self.lines[self.line][self.col :]
        if len(parts) == 1:
            self.lines[self.line] = head + text + tail
            self.col += len(text)
            return
        self.lines[self.line : self.line + 1] = (
            [head + parts[0]] + parts[1:-1] + [parts[-1] + tail]
        )
        self.line += len(parts) - 1
        self.col = len(parts[-1])

    def delete_before(self, count):
        for _ in range(count):
            if self.col > 0:
                line = self.lines[self.line]
                self.lines[self.line] = line[: self.col - 1] + line[self.col :]
                self.col -= 1

    def move_left(self, count):
        for _ in range(count):
            if self.col > 0:
                self.col -= 1
            elif self.line > 0:
                self.line -= 1
                self.col = len(self.lines[self.line])


KEYWORDS = {
    "import", "from", "def", "return", "try", "except", "finally", "as",
    "class", "if", "elif", "else", "for", "while", "with", "lambda",
    "None", "True", "False", "raise", "pass", "yield",
}

TOKEN_RE = re.compile(
    r'(""".*?"""|"[^"]*"|\'[^\']*\'|#.*$|--.*$|//.*$|\b\d+\b|\b[A-Za-z_][A-Za-z0-9_]*\b)'
)


LANGUAGE_KEYWORDS = {
    "python": KEYWORDS,
    "sql": {
        "SELECT", "DISTINCT", "FROM", "WHERE", "GROUP", "BY", "HAVING", "ORDER",
        "JOIN", "LEFT", "RIGHT", "INNER", "OUTER", "ON", "AS", "AND", "OR", "NOT",
        "NULL", "CREATE", "TABLE", "PRIMARY", "KEY", "DEFAULT", "CURRENT_TIMESTAMP",
        "WITH", "INSERT", "INTO", "VALUES", "UPDATE", "SET", "DELETE", "OVER",
        "PARTITION", "LIMIT", "OFFSET", "CASE", "WHEN", "THEN", "ELSE", "END",
        "UNION", "ALL", "EXISTS", "DESC", "ASC", "TEXT", "INTEGER", "TIMESTAMP",
        "NUMERIC", "BOOLEAN", "SUM", "COUNT", "AVG", "MIN", "MAX", "ROW_NUMBER",
        "RANK", "COALESCE",
    },
    "js": {
        "async", "await", "function", "const", "let", "var", "return", "class",
        "constructor", "new", "throw", "try", "catch", "finally", "if", "else",
        "for", "of", "in", "this", "export", "import", "from", "default", "null",
        "true", "false", "typeof", "extends", "super",
    },
}


# Language label for the window chrome, keyed by scenario syntax family.
def spans_for(line, language="python"):
    """Minimal syntax colouring for the pack demos: (start, end, colour) spans."""
    keywords = LANGUAGE_KEYWORDS.get(language, KEYWORDS)
    spans = []
    for match in TOKEN_RE.finditer(line):
        token = match.group(0)
        start, end = match.span()
        if token.startswith('"""') or token[0] in "\"'":
            color = GREEN
        elif token.startswith(("#", "--", "//")):
            color = FAINT
        elif token.isdigit():
            color = YELLOW
        elif token in keywords or token.upper() in keywords:
            color = MAUVE
        elif language == "python" and line[max(0, start - 4) : start].endswith("def "):
            color = BLUE
        elif language == "js" and line[max(0, start - 9) : start].endswith("function "):
            color = BLUE
        else:
            continue
        spans.append((start, end, color))
    return spans


class Renderer:
    """Paints one app window per pack: code editors, compose windows, PR and
    ticket forms. ``header`` is an ordered list of (label, value) rows drawn
    under the title bar; an empty header means a code editor with a gutter."""

    def __init__(self, app, window_title="draft.py", syntax=True, chrome="editor",
                 header=None, font_px=FONT_PX, line_h=LINE_H, language="python"):
        self.window_title = window_title
        self.syntax = syntax
        self.chrome = chrome
        self.header = header or {}
        self.rows = (
            list(self.header.items())
            if isinstance(self.header, dict)
            else list(self.header)
        )
        self.language = language
        self.line_h = line_h
        self.is_editor = not self.rows
        # Terminals have no line-number gutter; email/PR/ticket forms use header rows.
        self.gutter = self.is_editor and chrome != "terminal"
        self.text_x = TEXT_X if self.gutter else MARGIN + 26
        if self.rows:
            self.text_top = MARGIN + TITLE_H + 34 + 22 * len(self.rows)
        elif self.gutter:
            self.text_top = TEXT_TOP
        else:
            self.text_top = MARGIN + TITLE_H + 22
        self.mono = QFont("DejaVu Sans Mono")
        self.mono.setPixelSize(font_px)
        self.mono.setStyleStrategy(QFont.PreferAntialias)
        self.metrics = QFontMetricsF(self.mono)
        self.advance = self.metrics.horizontalAdvance("M")

        self.small = QFont("DejaVu Sans Mono")
        self.small.setPixelSize(13)
        self.small_metrics = QFontMetricsF(self.small)

        self.email_value = QFont("DejaVu Sans")
        self.email_value.setPixelSize(13)

        self.brand = QFont("DejaVu Sans")
        self.brand.setPixelSize(13)
        self.brand.setWeight(QFont.DemiBold)
        self.brand_metrics = QFontMetricsF(self.brand)

    def draw_code_line(self, p, text, spans, baseline):
        """Draw one line, colouring each token span (monospace, so x is exact)."""
        pos = 0
        p.setPen(TEXT)
        for start, end, color in spans:
            if start > pos:
                p.drawText(QPointF(self.text_x + self.advance * pos, baseline), text[pos:start])
            p.setPen(color)
            p.drawText(QPointF(self.text_x + self.advance * start, baseline), text[start:end])
            p.setPen(TEXT)
            pos = end
        if pos < len(text):
            p.drawText(QPointF(self.text_x + self.advance * pos, baseline), text[pos:])

    def frame(self, editor, highlight_len=0, caret=True):
        img = QImage(W, H, QImage.Format_RGB32)
        img.fill(MANTLE)
        p = QPainter(img)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setRenderHint(QPainter.TextAntialiasing, True)

        # window
        p.setPen(QPen(BORDER, 1.2))
        p.setBrush(BASE)
        p.drawRoundedRect(QRectF(MARGIN, MARGIN, W - 2 * MARGIN, H - 2 * MARGIN), 14, 14)

        # title bar
        for i, color in enumerate((RED, YELLOW, GREEN)):
            p.setPen(Qt.NoPen)
            p.setBrush(color)
            p.drawEllipse(QPointF(MARGIN + 24 + i * 21, MARGIN + TITLE_H / 2), 6.2, 6.2)

        p.setPen(QPen(BORDER, 1))
        p.drawLine(
            QPointF(MARGIN + 1, MARGIN + TITLE_H),
            QPointF(W - MARGIN - 1, MARGIN + TITLE_H),
        )

        p.setFont(self.small)
        p.setPen(SUBTLE)
        p.drawText(
            QPointF(MARGIN + 92, MARGIN + TITLE_H / 2 + self.small_metrics.ascent() / 2 - 2),
            self.window_title,
        )

        # brand, top right
        brand_text = "Textr"
        brand_x = W - MARGIN - 18 - self.brand_metrics.horizontalAdvance(brand_text)
        brand_color = QColor(MAUVE)
        brand_color.setAlpha(150)
        p.setFont(self.brand)
        p.setPen(brand_color)
        p.drawText(
            QPointF(brand_x, MARGIN + TITLE_H / 2 + self.brand_metrics.ascent() / 2 - 2),
            brand_text,
        )

        if not self.is_editor:
            # form header (To/Subject, ticket fields, PR title) instead of a gutter
            row_y = MARGIN + TITLE_H + 16
            for label, value in self.rows:
                p.setFont(self.small)
                p.setPen(FAINT)
                p.drawText(QPointF(MARGIN + 24, row_y), label)
                p.setFont(self.email_value)
                p.setPen(TEXT)
                p.drawText(QPointF(MARGIN + 96, row_y), value)
                row_y += 22
            p.setPen(QPen(BORDER, 1))
            p.drawLine(QPointF(MARGIN + 1, row_y - 6), QPointF(W - MARGIN - 1, row_y - 6))
        elif self.gutter:
            # gutter
            p.setFont(self.small)
            gutter_right = self.text_x - 20
            for i in range(len(editor.lines)):
                baseline = self.text_top + i * self.line_h + self.metrics.ascent()
                num = str(i + 1)
                p.setPen(FAINT)
                p.drawText(
                    QPointF(gutter_right - self.small_metrics.horizontalAdvance(num), baseline),
                    num,
                )

        # trigger highlight (drawn behind the text)
        if highlight_len > 0:
            x0 = self.text_x + self.advance * (editor.col - highlight_len)
            width = self.advance * highlight_len
            baseline = self.text_top + editor.line * self.line_h + self.metrics.ascent()
            top = baseline - self.metrics.ascent() + 1
            p.setPen(Qt.NoPen)
            highlight = QColor(MAUVE)
            highlight.setAlpha(64)
            p.setBrush(highlight)
            p.drawRoundedRect(
                QRectF(x0 - 2, top, width + 4, self.metrics.height()), 3, 3
            )

        # code
        p.setFont(self.mono)
        for i, line in enumerate(editor.lines):
            if line:
                baseline = self.text_top + i * self.line_h + self.metrics.ascent()
                self.draw_code_line(
                    p, line, spans_for(line, self.language) if self.syntax else [], baseline
                )

        # caret
        if caret:
            cx = self.text_x + self.advance * editor.col
            baseline = self.text_top + editor.line * self.line_h + self.metrics.ascent()
            top = baseline - self.metrics.ascent() + 2
            p.setPen(Qt.NoPen)
            p.setBrush(MAUVE)
            p.drawRoundedRect(QRectF(cx, top, 2.4, self.metrics.height() - 3), 1.2, 1.2)

        p.end()
        return img

    def frame_list(self, rows, shown, banner="", banner_color=None):
        """Manager window: a snippet table that fills in row by row.

        Used for the espanso page, where the pitch is importing a match file
        rather than typing a trigger: the rows are espanso-style triggers and
        the banner reports the import.
        """
        img = QImage(W, H, QImage.Format_RGB32)
        img.fill(MANTLE)
        p = QPainter(img)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setRenderHint(QPainter.TextAntialiasing, True)

        p.setPen(QPen(BORDER, 1.2))
        p.setBrush(BASE)
        p.drawRoundedRect(QRectF(MARGIN, MARGIN, W - 2 * MARGIN, H - 2 * MARGIN), 14, 14)

        for i, color in enumerate((RED, YELLOW, GREEN)):
            p.setPen(Qt.NoPen)
            p.setBrush(color)
            p.drawEllipse(QPointF(MARGIN + 24 + i * 21, MARGIN + TITLE_H / 2), 6.2, 6.2)

        p.setPen(QPen(BORDER, 1))
        p.drawLine(
            QPointF(MARGIN + 1, MARGIN + TITLE_H),
            QPointF(W - MARGIN - 1, MARGIN + TITLE_H),
        )
        p.setFont(self.small)
        p.setPen(SUBTLE)
        p.drawText(
            QPointF(MARGIN + 92, MARGIN + TITLE_H / 2 + self.small_metrics.ascent() / 2 - 2),
            self.window_title,
        )

        brand_text = "Textr"
        brand_x = W - MARGIN - 18 - self.brand_metrics.horizontalAdvance(brand_text)
        brand_color = QColor(MAUVE)
        brand_color.setAlpha(150)
        p.setFont(self.brand)
        p.setPen(brand_color)
        p.drawText(
            QPointF(brand_x, MARGIN + TITLE_H / 2 + self.brand_metrics.ascent() / 2 - 2),
            brand_text,
        )

        # Banner row is always reserved so the table never jumps when it changes.
        top = MARGIN + TITLE_H + 22
        p.setFont(self.small)
        p.setPen(banner_color if banner_color is not None else SUBTLE)
        p.drawText(QPointF(MARGIN + 26, top), banner)
        top += 26
        p.setPen(QPen(BORDER, 1))
        p.drawLine(QPointF(MARGIN + 20, top - 12), QPointF(W - MARGIN - 20, top - 12))

        p.setFont(self.small)
        p.setPen(FAINT)
        p.drawText(QPointF(MARGIN + 26, top + 8), "TRIGGER")
        p.drawText(QPointF(MARGIN + 150, top + 8), "EXPANDS TO")
        top += 26

        p.setFont(self.mono)
        p.setClipRect(
            QRectF(MARGIN + 26, top, W - 2 * MARGIN - 52, max(1, len(rows)) * self.line_h + 6)
        )
        for i, (trigger, value) in enumerate(rows[:shown]):
            baseline = top + i * self.line_h + self.metrics.ascent()
            p.setPen(MAUVE)
            p.drawText(QPointF(MARGIN + 26, baseline), trigger)
            p.setPen(TEXT)
            p.drawText(QPointF(MARGIN + 150, baseline), value.split("\n")[0])
        p.setClipping(False)

        p.end()
        return img


def apply_expansion(editor, trigger, expansion):
    """Mirror Daemon.replace(): drop the trigger, insert, honour {cursor}."""
    editor.delete_before(len(trigger))
    before, marker, after = expansion.partition("{cursor}")
    if marker:
        editor.insert(before + after)
        editor.move_left(len(after))
    else:
        editor.insert(expansion)


def build_frames(out_dir, renderer, daemon, editor):
    """Walk a fixed timeline, writing one PNG per frame. Returns frame count."""
    counter = {"n": 0}
    pending = {"value": None}

    def original_replace(trigger, expansion):
        pending["value"] = (trigger, expansion)
        daemon.buffer = ""
        daemon.is_expanding = False

    daemon.replace = original_replace

    def snap(typing=False):
        caret = True if typing else (counter["n"] // 8) % 2 == 0
        buffer = daemon.buffer
        highlight = len(buffer) if buffer and any(t.startswith(buffer) for t in DEMO_SNIPPETS) else 0
        img = renderer.frame(editor, highlight_len=highlight, caret=caret)
        img.save(os.path.join(out_dir, f"frame_{counter['n']:05d}.png"))
        counter["n"] += 1

    def hold(n, typing=False):
        for _ in range(n):
            snap(typing)

    def press(char=None, name=None):
        daemon._handle_key(Key(char=char) if char is not None else Key(name=name))
        if pending["value"] is not None:
            trigger, expansion = pending["value"]
            pending["value"] = None
            apply_expansion(editor, trigger, expansion)

    def type_text(text, gap=3):
        for ch in text:
            editor.insert(ch)
            press(char=ch)
            snap(True)
            hold(gap - 1, True)

    hold(10)                       # idle, caret blinking
    type_text(":py")               # expands the moment the trigger completes
    snap(True)
    hold(16)

    # jump to the end of the function, then start a new line
    editor.line = len(editor.lines) - 1
    editor.col = len(editor.lines[editor.line])
    press(name="enter")
    editor.insert("\n")
    snap(True)
    hold(4)

    type_text(":try")
    snap(True)
    hold(26)

    return counter["n"], daemon


def build_frames_script(out_dir, renderer, daemon, editor, snippets, steps, prompt=""):
    """Drive a scripted timeline (used by the pack demos)."""
    counter = {"n": 0}
    pending = {"value": None}

    def original_replace(trigger, expansion):
        pending["value"] = (trigger, expansion)
        daemon.buffer = ""
        daemon.is_expanding = False

    daemon.replace = original_replace

    def snap(typing=False):
        caret = True if typing else (counter["n"] // 8) % 2 == 0
        buffer = daemon.buffer
        highlight = len(buffer) if buffer and any(t.startswith(buffer) for t in snippets) else 0
        img = renderer.frame(editor, highlight_len=highlight, caret=caret)
        img.save(os.path.join(out_dir, "frame_%05d.png" % counter["n"]))
        counter["n"] += 1

    def hold(n, typing=False):
        for _ in range(n):
            snap(typing)

    def press(char=None, name=None):
        daemon._handle_key(Key(char=char) if char is not None else Key(name=name))
        if pending["value"] is not None:
            trigger, expansion = pending["value"]
            pending["value"] = None
            apply_expansion(editor, trigger, expansion)

    def type_text(text, gap=2):
        for ch in text:
            editor.insert(ch)
            press(char=ch)
            snap(True)
            hold(gap - 1, True)

    for kind, payload in steps:
        if kind == "hold":
            hold(payload)
        elif kind == "type":
            type_text(payload)
        elif kind == "snap":
            snap(True)
        elif kind == "newline":
            editor.line = len(editor.lines) - 1
            editor.col = len(editor.lines[editor.line])
            press(name="enter")
            # Terminals re-print their prompt; every other scenario passes "".
            editor.insert("\n" + prompt)
            snap(True)
            hold(4)
        elif kind == "run":
            # Press Enter like a human, then print the output the shell returns.
            editor.line = len(editor.lines) - 1
            editor.col = len(editor.lines[editor.line])
            press(name="enter")
            for line in payload:
                editor.insert("\n" + line)
            editor.insert("\n" + prompt)
            snap(True)
            hold(9)

    return counter["n"]


def build_frames_import(out_dir, renderer, rows, banner_reading, banner_done):
    """Fill the manager table row by row, then swap the banner to the summary."""
    counter = {"n": 0}

    def snap(shown, banner, color):
        img = renderer.frame_list(rows, shown, banner, color)
        img.save(os.path.join(out_dir, "frame_%05d.png" % counter["n"]))
        counter["n"] += 1

    for _ in range(10):
        snap(0, banner_reading, None)
    for shown in range(1, len(rows) + 1):
        for _ in range(3):
            snap(shown, banner_reading, None)
    for _ in range(28):
        snap(len(rows), banner_done, GREEN)

    return counter["n"]


def write_contact_sheet(frames_dir, count, out_path):
    """2x2 montage of representative frames, for a quick visual check."""
    picks = [0, count // 3, (2 * count) // 3, count - 1]
    cell_w, cell_h = W // 2, H // 2
    sheet = QImage(W, H, QImage.Format_RGB32)
    sheet.fill(QColor("#000000"))
    p = QPainter(sheet)
    for index, frame_no in enumerate(picks):
        img = QImage(os.path.join(frames_dir, f"frame_{frame_no:05d}.png"))
        x = (index % 2) * cell_w
        y = (index // 2) * cell_h
        p.drawImage(QRectF(x, y, cell_w, cell_h), img)
    p.end()
    sheet.save(out_path)
    return picks


def encode_gif(frames_dir, count, out_path):
    palette = os.path.join(frames_dir, "palette.png")
    pattern = os.path.join(frames_dir, "frame_%05d.png")
    subprocess.run(
        [
            "ffmpeg", "-y", "-loglevel", "error",
            "-framerate", str(FPS), "-i", pattern,
            "-vf", "palettegen=stats_mode=diff:max_colors=112",
            palette,
        ],
        check=True,
    )
    subprocess.run(
        [
            "ffmpeg", "-y", "-loglevel", "error",
            "-framerate", str(FPS), "-i", pattern, "-i", palette,
            "-lavfi", "paletteuse=dither=none:diff_mode=rectangle",
            "-loop", "0",
            out_path,
        ],
        check=True,
    )


def render_gif(name, output, args):
    """Render one scenario to a GIF (demo = README animation, packs = one each)."""
    app = QApplication.instance() or QApplication([])

    if name == "demo":
        meta = {"snippets": DEMO_SNIPPETS, "start": START_LINES,
                "title": "draft.py", "syntax": True, "steps": None}
    else:
        meta = PACK_SCENARIOS[name]
    check_personal_data(name, [meta, DEMO_CONFIG])

    snippets, start_lines = meta["snippets"], meta["start"]
    title, syntax, steps = meta["title"], meta["syntax"], meta["steps"]

    config_dir = tempfile.mkdtemp(prefix="textr-demo-")
    os.environ["TEXTR_CONFIG_DIR"] = config_dir
    with open(os.path.join(config_dir, "snippets.json"), "w") as handle:
        json.dump(snippets, handle, indent=2)
    with open(os.path.join(config_dir, "config.json"), "w") as handle:
        json.dump(DEMO_CONFIG, handle, indent=2)

    daemon = daemon_module.Daemon()
    if name == "python" and daemon.current_language != "python":
        daemon.set_language("python")

    frames_dir = tempfile.mkdtemp(prefix="textr-frames-")
    renderer = Renderer(
        app,
        window_title=title,
        syntax=syntax,
        chrome=meta.get("chrome", "editor"),
        header=meta.get("header"),
        font_px=meta.get("font_px", FONT_PX),
        line_h=meta.get("line_h", LINE_H),
        language=meta.get("language", "python"),
    )
    editor = Editor(start_lines)
    if meta.get("caret_col"):
        editor.col = meta["caret_col"]

    try:
        if meta.get("mode") == "import":
            count = build_frames_import(
                frames_dir,
                renderer,
                meta["rows"],
                meta["banner_reading"],
                meta["banner_done"],
            )
        elif steps is None:
            count, _ = build_frames(frames_dir, renderer, daemon, editor)
        else:
            count = build_frames_script(
                frames_dir, renderer, daemon, editor, snippets, steps,
                prompt=meta.get("prompt", ""),
            )
        os.makedirs(os.path.dirname(os.path.abspath(output)), exist_ok=True)
        encode_gif(frames_dir, count, output)

        size_kb = os.path.getsize(output) / 1024
        print(f"wrote {output}  ({count} frames, {count / FPS:.1f}s, {size_kb:.0f} KB)")

        if args.contact_sheet:
            picks = write_contact_sheet(frames_dir, count, args.contact_sheet)
            print(f"wrote {args.contact_sheet}  (frames {picks})")
        if args.keep_frames:
            keep = os.path.join(ROOT, "assets", "frames")
            shutil.rmtree(keep, ignore_errors=True)
            shutil.copytree(frames_dir, keep)
            print(f"kept frames in {keep}")
    finally:
        shutil.rmtree(frames_dir, ignore_errors=True)
        shutil.rmtree(config_dir, ignore_errors=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-o", "--output", default=None,
                        help="output path (defaults per scenario)")
    parser.add_argument("--scenario", default="demo", choices=list(DEFAULT_OUTPUTS))
    parser.add_argument("--all-packs", action="store_true",
                        help="build every pack GIF (all except the README demo)")
    parser.add_argument("--contact-sheet", default=None, help="write a 2x2 PNG preview")
    parser.add_argument("--keep-frames", action="store_true", help="keep extracted PNG frames")
    args = parser.parse_args()

    if shutil.which("ffmpeg") is None:
        sys.exit("ffmpeg not found: install it to encode the GIF")

    if args.all_packs:
        targets = [
            (name, DEFAULT_OUTPUTS[name])
            for name in DEFAULT_OUTPUTS
            if name != "demo" and name not in SITE_DEMOS
        ]
    else:
        targets = [(args.scenario, args.output or DEFAULT_OUTPUTS[args.scenario])]

    for name, output in targets:
        render_gif(name, output, args)


if __name__ == "__main__":
    main()
