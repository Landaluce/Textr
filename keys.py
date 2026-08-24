"""Normalized key representation shared by the input backends.

pynput (X11/XWayland) and the evdev listener (native Wayland) both translate
raw platform key events into this form, so the daemon's core key handler is
backend-agnostic: it only looks at ``key.name`` (special keys like "enter",
"backspace") and ``key.char`` (printable characters).
"""


class Key:
    """A normalized key event: either a printable character or a named special key."""

    __slots__ = ("name", "char")

    def __init__(self, name=None, char=None):
        self.name = name
        self.char = char

    def __repr__(self):
        return f"Key(name={self.name!r}, char={self.char!r})"

    def __eq__(self, other):
        return (
            isinstance(other, Key)
            and self.name == other.name
            and self.char == other.char
        )

    def __hash__(self):
        return hash((self.name, self.char))
