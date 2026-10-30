# texter_mvp/snippet_helpers.py
"""Shared snippet helpers: placeholders, scoring, expansion, history."""
import re
from copy import deepcopy
from datetime import datetime


BUILTIN_PLACEHOLDERS = frozenset(
    {"name", "email", "date", "time", "iso", "week", "clipboard"}
)

SETTING_PLACEHOLDER_RE = re.compile(
    r"(?<![\{$])\{([A-Za-z_][A-Za-z0-9_]*)\}(?!\})"
)

def _fuzzy_score(trigger, expansion, query):
    """Score for the quick picker: substring beats subsequence beats none."""
    if not query:
        return 1
    t = trigger.lower()
    e = (expansion or "").lower()
    if query in t:
        return 100 - (len(t) - len(query))
    if query in e:
        return 60
    # Subsequence match in the trigger (e.g. "sig" in "signature").
    it = iter(t)
    if all(ch in it for ch in query):
        return 40
    return 0

def _normalized_expansion(snippet):
    """Comparable expansion text for duplicate detection."""
    kind = snippet.get("type", "variable")
    if kind == "variable":
        text = snippet.get("value", "")
    elif kind == "template":
        languages = snippet.get("languages", {})
        text = " | ".join(
            f"{lang}:{value}"
            for lang, value in sorted(languages.items())
            if isinstance(value, str)
        ) if isinstance(languages, dict) else ""
    elif kind == "command":
        text = str(snippet.get("action", ""))
    else:
        text = ""
    return " ".join(str(text).split())

def history_entry(snippet):
    """One history entry: when it was saved + the snippet as stored."""
    snap = {
        k: deepcopy(v) for k, v in (snippet or {}).items() if k != "history"
    }
    return {
        "saved_at": datetime.now().isoformat(timespec="seconds"),
        "snippet": snap,
    }
