# texter_mvp/snippet_helpers.py
"""Shared snippet helpers: placeholders, scoring, expansion, history."""
import re
from copy import deepcopy
from datetime import datetime


BUILTIN_PLACEHOLDERS = frozenset(
    {"name", "email", "date", "time", "iso", "week", "clipboard",
     "counter", "random"}
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
    elif kind == "shell":
        text = snippet.get("value", "")
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


def parse_espanso_matches(text):
    """Parse an Espanso match file (YAML subset) into snippet dicts.

    Returns [{"trigger": ..., "replace": ..., "label": ...}, ...] for
    every entry under the top-level `matches:` list that has both a
    trigger and a replace. Other keys are ignored. `label:` becomes
    the snippet note. `{{...}}` sequences (Espanso variables) are left
    as literal text: Textr does not evaluate them.

    Supported scalars: plain, single-quoted, double-quoted (with the
    common \\n \\t \\" \\\\ escapes), literal blocks (`|`) and
    folded blocks (`>`), including `-`/`+` chomping indicators. This is
    a purpose-built parser, not a YAML implementation: multi-line
    quoted scalars are joined with newlines, and anything it cannot
    parse falls back to the plain-scalar reading of the line.
    """
    lines = (text or "").splitlines()
    # Locate the top-level `matches:` key; nothing before it is read.
    matches_indent = None
    start = None
    for i, line in enumerate(lines):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())
        if line.strip() in ("---", "..."):
            continue
        if indent == 0 and line.strip() == "matches:":
            matches_indent = 0
            start = i + 1
            break
        if indent == 0:
            # Another top-level key first: no usable matches list.
            return []
    if start is None:
        return []

    def _indent_of(line):
        return len(line) - len(line.lstrip())

    def _parse_scalar(first, rest_lines, base_indent):
        """Parse one scalar value.

        `first` is the text after `key:` on the key line; `rest_lines`
        are the following (line, indent) pairs. Returns (value, used)
        where `used` is how many of rest_lines the scalar consumed.
        """
        stripped = first.strip()
        if not stripped:
            return "", 0
        marker = stripped[0]
        if marker in ("|", ">"):
            # Block scalar: chomping/indent indicators after the marker.
            folded = marker == ">"
            chomp = "clip"
            for ch in stripped[1:]:
                if ch == "-":
                    chomp = "strip"
                elif ch == "+":
                    chomp = "keep"
                elif ch.isdigit():
                    continue
                else:
                    break
            content = []
            used = 0
            for line, indent in rest_lines:
                if not line.strip():
                    content.append("")
                    used += 1
                    continue
                if indent <= base_indent:
                    break
                content.append(line)
                used += 1
            # Drop the trailing blank lines the loop collected past
            # the block; chomping decides what survives at the end.
            while content and content[-1] == "":
                content.pop()
            # Re-indent: strip the common leading whitespace.
            indents = [_indent_of(l) for l in content if l.strip()]
            cut = min(indents) if indents else 0
            body = [l[cut:] if len(l) >= cut else "" for l in content]
            if folded:
                paras = []
                current = []
                for line in body:
                    if line == "":
                        if current:
                            paras.append(" ".join(current))
                            current = []
                        paras.append("")
                    else:
                        current.append(line)
                if current:
                    paras.append(" ".join(current))
                value = "\n".join(paras)
            else:
                value = "\n".join(body)
            if chomp == "clip":
                value = value + "\n" if value else ""
            elif chomp == "keep":
                value = value + "\n" if value else ""
            # Snippet values never keep a trailing newline: strip the
            # single newline clip chomping adds.
            return value.rstrip("\n"), used
        if marker in ("'", '"'):
            quote = marker
            collected = [stripped]
            used = 0
            body = stripped[1:]
            # Consume continuation lines until the closing quote.
            while True:
                if quote == "'":
                    # An odd run of trailing quotes ends the scalar
                    # (a doubled '' inside is an escaped quote).
                    j = len(body) - 1
                    run = 0
                    while j >= 0 and body[j] == "'":
                        run += 1
                        j -= 1
                    closed = run % 2 == 1
                else:
                    closed = False
                    k = 0
                    while k < len(body):
                        if body[k] == "\\":
                            k += 2
                            continue
                        if body[k] == '"':
                            closed = True
                            break
                        k += 1
                if closed or used >= len(rest_lines):
                    break
                line, _indent = rest_lines[used]
                used += 1
                body += "\n" + line.strip()
                collected.append(line.strip())
            raw = "\n".join(collected)
            if len(raw) >= 2 and raw.startswith(quote):
                # Find the matching close, honouring backslash escapes
                # for double quotes and doubled quotes for single.
                inner = raw[1:]
                if quote == "'":
                    inner = inner[:-1] if inner.endswith("'") else inner
                    return inner.replace("''", "'"), used
                out = []
                k = 0
                while k < len(inner):
                    ch = inner[k]
                    if ch == "\\" and k + 1 < len(inner):
                        nxt = inner[k + 1]
                        mapping = {"n": "\n", "t": "\t", "r": "\r",
                                   '"': '"', "\\": "\\", "0": "\0"}
                        out.append(mapping.get(nxt, nxt))
                        k += 2
                        continue
                    if ch == '"':
                        break
                    out.append(ch)
                    k += 1
                return "".join(out), used
            return raw, used
        # Plain scalar: strip a trailing " # comment" (YAML rule: a
        # space before # starts a comment in plain scalars).
        value = stripped
        idx = value.find(" #")
        if idx >= 0:
            value = value[:idx].rstrip()
        return value, 0

    entries = []
    current = None
    current_indent = None

    def _flush():
        nonlocal current
        if current is not None:
            trigger = (current.get("trigger") or "").strip()
            replace = current.get("replace") or ""
            if trigger and replace:
                entries.append({
                    "trigger": trigger,
                    "replace": replace,
                    "label": (current.get("label") or "").strip(),
                })
        current = None

    i = start
    while i < len(lines):
        line = lines[i]
        if not line.strip():
            i += 1
            continue
        indent = _indent_of(line)
        stripped = line.strip()
        if stripped.startswith("#"):
            i += 1
            continue
        # A new list item starts a new entry.
        item_match = re.match(r"^-\s+(.*)$", stripped)
        if indent > matches_indent and item_match is not None:
            _flush()
            current = {}
            current_indent = indent
            rest = item_match.group(1)
            if rest:
                # First key inline after the dash: rewrite as a key
                # line at this indent and fall through to key parsing.
                stripped = rest
                indent = indent + 2
            else:
                i += 1
                continue
        if current is None:
            # Lines outside any list item (other top-level keys): stop
            # only at a dedent to the matches level or below.
            i += 1
            continue
        if indent <= matches_indent:
            break
        key_match = re.match(r"^([A-Za-z_][A-Za-z0-9_-]*)\s*:\s*(.*)$", stripped)
        if key_match is None:
            # Continuation of a plain multi-line scalar: Espanso
            # values are single-line except blocks; ignore it.
            i += 1
            continue
        key, first = key_match.group(1), key_match.group(2)
        rest_lines = []
        j = i + 1
        while j < len(lines):
            rest_lines.append((lines[j], _indent_of(lines[j])))
            j += 1
        if key in ("trigger", "replace", "label"):
            value, used = _parse_scalar(first, rest_lines, indent)
            current[key] = value
            # Skip lines the scalar consumed.
            i += 1 + used
            continue
        # Ignored key: still skip a block scalar attached to it.
        if first.strip() and first.strip()[0] in ("|", ">"):
            _value, used = _parse_scalar(first, rest_lines, indent)
            i += 1 + used
            continue
        i += 1
    _flush()
    return entries
