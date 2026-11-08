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

TOKEN_RE = re.compile(r'(""".*?"""|"[^"]*"|\'[^\']*\'|#.*$|\b\d+\b|\b[A-Za-z_][A-Za-z0-9_]*\b)')


def spans_for(line):
    """Minimal Python-ish syntax colouring: (start, end, colour) spans."""
    spans = []
    for match in TOKEN_RE.finditer(line):
        token = match.group(0)
        start, end = match.span()
        if token.startswith('"""') or token[0] in "\"'":
            color = GREEN
        elif token.startswith("#"):
            color = FAINT
        elif token.isdigit():
            color = YELLOW
        elif token in KEYWORDS:
            color = MAUVE
        elif line[max(0, start - 4) : start].endswith("def "):
            color = BLUE
        else:
            continue
        spans.append((start, end, color))
    return spans


class Renderer:
    def __init__(self, app):
        self.mono = QFont("DejaVu Sans Mono")
        self.mono.setPixelSize(FONT_PX)
        self.mono.setStyleStrategy(QFont.PreferAntialias)
        self.metrics = QFontMetricsF(self.mono)
        self.advance = self.metrics.horizontalAdvance("M")

        self.small = QFont("DejaVu Sans Mono")
        self.small.setPixelSize(13)
        self.small_metrics = QFontMetricsF(self.small)

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
                p.drawText(QPointF(TEXT_X + self.advance * pos, baseline), text[pos:start])
            p.setPen(color)
            p.drawText(QPointF(TEXT_X + self.advance * start, baseline), text[start:end])
            p.setPen(TEXT)
            pos = end
        if pos < len(text):
            p.drawText(QPointF(TEXT_X + self.advance * pos, baseline), text[pos:])

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
            "draft.py",
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

        # gutter
        p.setFont(self.small)
        gutter_right = TEXT_X - 20
        for i in range(len(editor.lines)):
            baseline = TEXT_TOP + i * LINE_H + self.metrics.ascent()
            num = str(i + 1)
            p.setPen(FAINT)
            p.drawText(
                QPointF(gutter_right - self.small_metrics.horizontalAdvance(num), baseline),
                num,
            )

        # trigger highlight (drawn behind the text)
        if highlight_len > 0:
            x0 = TEXT_X + self.advance * (editor.col - highlight_len)
            width = self.advance * highlight_len
            baseline = TEXT_TOP + editor.line * LINE_H + self.metrics.ascent()
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
                baseline = TEXT_TOP + i * LINE_H + self.metrics.ascent()
                self.draw_code_line(p, line, spans_for(line), baseline)

        # caret
        if caret:
            cx = TEXT_X + self.advance * editor.col
            baseline = TEXT_TOP + editor.line * LINE_H + self.metrics.ascent()
            top = baseline - self.metrics.ascent() + 2
            p.setPen(Qt.NoPen)
            p.setBrush(MAUVE)
            p.drawRoundedRect(QRectF(cx, top, 2.4, self.metrics.height() - 3), 1.2, 1.2)

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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-o", "--output", default=os.path.join(ROOT, "assets", "demo.gif"))
    parser.add_argument("--contact-sheet", default=None, help="write a 2x2 PNG preview")
    parser.add_argument("--keep-frames", action="store_true", help="keep extracted PNG frames")
    args = parser.parse_args()

    if shutil.which("ffmpeg") is None:
        sys.exit("ffmpeg not found: install it to encode the GIF")

    app = QApplication([])

    config_dir = tempfile.mkdtemp(prefix="textr-demo-")
    os.environ["TEXTR_CONFIG_DIR"] = config_dir
    with open(os.path.join(config_dir, "snippets.json"), "w") as handle:
        json.dump(DEMO_SNIPPETS, handle, indent=2)
    with open(os.path.join(config_dir, "config.json"), "w") as handle:
        json.dump(DEMO_CONFIG, handle, indent=2)

    daemon = daemon_module.Daemon()
    if daemon.current_language != "python":
        daemon.set_language("python")

    frames_dir = tempfile.mkdtemp(prefix="textr-frames-")
    renderer = Renderer(app)
    editor = Editor(START_LINES)

    try:
        count, _ = build_frames(frames_dir, renderer, daemon, editor)
        os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
        encode_gif(frames_dir, count, args.output)

        size_kb = os.path.getsize(args.output) / 1024
        print(f"wrote {args.output}  ({count} frames, {count / FPS:.1f}s, {size_kb:.0f} KB)")

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


if __name__ == "__main__":
    main()
