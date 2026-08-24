# Textr

Text expansion for Linux (X11 and Wayland). Type a short trigger anywhere and Textr expands it into full text, code templates, or actions — with a PySide6 GUI for managing snippets and settings.

## Features

- **Global trigger detection** — works in any application via a background keyboard daemon
- **Three snippet types** — static text (`variable`), per-language code (`template`), and actions (`command`)
- **Two insertion modes** — fast clipboard paste (default) or human-like typing ("Human Typing")
- **Word-boundary matching** — triggers only fire after whitespace/punctuation, never mid-word
- **Prefix disambiguation** — when one trigger is the prefix of another (`:d` vs `:dt`), expansion waits until the next key reveals which one you meant; the longer trigger wins
- **Grouped snippet manager** — group snippets by category (with `python/web`-style nested folders), by type, or by tag, with an optional second "then by" level (default: category → tag); search filters as you type; sort by name, recently used, or most used
- **Tags & usage tracking** — snippets carry multiple comma-separated tags, and the daemon records how often and when each snippet expands
- **Template variables** — `{name}`, `{email}`, `{date}`, `{time}`, `{iso}`, `{week}`, `{clipboard}`, plus `{cursor}` to leave the caret mid-expansion
- **Per-snippet enable/disable** — turn a snippet off (edit → uncheck **Enabled**) without deleting it; disabled snippets are dimmed in the tree and never expand
- **Configurable date/time formats** — set the strftime patterns for `{date}`/`{time}` (and `:d`/`:time`/`:datetime`) in the profile settings, e.g. `%d/%m/%Y` for day-first dates
- **Safe import** — importing a JSON file warns about triggers it will overwrite before applying
- **Crash resilience** — the keyboard listener survives internal errors and restarts automatically instead of silently dying

## Requirements

- Linux (X11 or Wayland)
- Python 3.9+

```txt
PySide6>=6.6
pynput>=1.7
pyperclip>=1.9
evdev>=1.6; sys_platform == 'linux'
```

### Wayland notes

- **Input capture** uses `evdev` (raw `/dev/input/event*` reads) so triggers work in native Wayland apps. Your user needs read access to the input devices — either run as root or add your user to the `input` group and log in again. If evdev capture is unavailable, Textr falls back to pynput, which only sees XWayland apps.
- **Typing output** goes through `ydotool` (the daemon must be running). Install it from [https://github.com/ReimuNotMoe/ydotool](https://github.com/ReimuNotMoe/ydotool).
- **Keyboard layout** — evdev reports keycodes, not characters, so the captured keys must be mapped through your keyboard layout. Set it to `us`, `uk`, `de`, `fr`, or `es` in Settings (stored as `"layout"` in `config.json`; defaults to `us`). AltGr characters (e.g. `@` on a German layout) are supported. Layout tables are best-effort approximations of the common variants — extend `LAYOUTS` in `input_wayland.py` to add more.

## Installation

Either install as a package (recommended for regular use):

```bash
python3 -m pip install .        # installs the `textr` command
```

Or run from the source tree:

```bash
python3 -m pip install -r requirements.txt
bash run.sh                     # or: python3 main.py
```

## Usage

```bash
textr                            # installed as a package
bash run.sh                      # or: python3 main.py, from the source tree
```

This starts both the UI window and the background daemon. Close or hide the window; triggers keep working while it is hidden.

### Keyboard shortcuts

| Shortcut | Where | Action |
| -------- | ----- | ------ |
| `Ctrl+Shift+P` | anywhere (global) | Toggle the daemon pause/resume |
| `Ctrl+N` | manager window | Add a snippet |
| `F2` / `Enter` | manager window | Edit the selected snippet |
| `Del` | manager window | Delete the selected snippet |
| `Ctrl+Shift+V` | expansion (paste mode) | Insert the expansion text (configurable via **Settings → Paste shortcut**) |

Edit/Delete only fire when the snippet list has focus, so they never trigger while you're typing in the search box. The paste shortcut is a combo Textr *sends* to paste — it does not work in Human Typing mode (typed char-by-char) or on Wayland (typed via ydotool).

### Snippet types

| Type | Behavior |
| ------ | ---------- |
| `variable` | Expands to static text, e.g. trigger `sig` → your signature |
| `template` | Expands to language-specific code chosen by the Programming language selector |
| `command` | Performs an action (no text inserted), e.g. `:pause` toggles pause/resume |

Snippet JSON structure:

```json
{
  "trigger": "sig",
  "type": "variable",
  "category": "personal",
  "tags": ["personal", "work"],
  "value": "Best, {name} ({email})"
}
```

`tags` is optional (a snippet can appear under several groups); snippets loaded from older files without `tags` default to their `category`. The daemon also records `usage` (count) and `last_used` (ISO timestamp) on every expansion for the recently/most-used sorting. `"enabled": false` disables a snippet without deleting it (defaults to enabled when absent).

Templates use `{"python": "...", "javascript": "..."}` under `"languages"` and support the same `{variable}` substitution.

Put `{cursor}` anywhere in an expansion to leave the caret there after insertion instead of at the end — handy for filling in the middle of a snippet, e.g. `function f() { {cursor} }`.

### Built-in commands

Commands are just snippets of type `command` with an `action` field:

| Action | Effect |
| -------- | -------- |
| `toggle_pause` / `resume` | Pause or resume expansion |
| `set_language` | Switch the template language (`value` holds the language) |
| `date` / `time` / `datetime` | Insert the current date/time into the active window |
| `reload` | Re-read `snippets.json` from disk |
| `save` | Persist snippets and config |
| `ui` / `top` / `hide` | Show, raise, or hide the manager window |
| `restart` / `shutdown` | Restart or quit the app |

## Configuration & data files

Data files are looked up in this order:

1. `$TEXTR_CONFIG_DIR` (used by tests; unset normally)
2. a file already present next to the working directory (legacy source-tree layout)
3. `$XDG_CONFIG_HOME/textr/` (defaults to `~/.config/textr/`), created on demand

- `config.json` — user profile (`name`, `email`, `layout`, `language`, `paste_hotkey`, `date_format`, `time_format`)
- `snippets.json` — all snippet definitions

The paste shortcut used in clipboard mode defaults to `Ctrl+Shift+V` and is configurable
via **Settings → Paste shortcut** (e.g. `ctrl+insert`); if a terminal swallows the paste,
change it there rather than editing the config by hand.

They are personal data and are excluded from version control via `.gitignore`.

## Development

```bash
python3 -m pytest tests/
python3 -m diag              # environment report (devices, tools, permissions)
python3 -m diag --listen 5   # + live keycode capture under the selected layout
```

The test suite exercises the daemon without X11: the keyboard controller, clipboard, and sleeps are faked, so it runs headless in CI.

### Architecture

```text
main.py ── starts ──> TexterUI (Qt GUI thread)
                  └─> Daemon.run() on a worker thread
                        ├─ pynput Listener (X11)  -> on_press -> buffer + trigger match
                        │    └─ evdev WaylandInputListener (Wayland)
                        └─ hotkey (Ctrl+Shift+P): pynput GlobalHotKeys or evdev
```

Both input backends translate keys into a normalized `keys.Key` (`name`/`char`), so the daemon's core matching in `_handle_key` is backend-agnostic. Typing output on X11 uses the pynput controller + clipboard; on Wayland it uses ydotool.

Threading rules that keep the daemon alive:

- The GUI thread and the keyboard listener thread share `daemon.snippets`; every access goes through `snippets_lock`, and UI mutations go through the helper methods (`add_or_update_snippet`, `replace_snippet`, `delete_snippet`, `import_snippets`).
- Nothing inside the key handler may touch Qt widgets or raise — UI actions requested by command snippets are delivered to the GUI thread via the `daemon_command` Signal.

## Troubleshooting

- **Triggers not expanding** — the daemon may be paused: press `Ctrl+Shift+P` once, or check the Daemon button in the Texter window.
- **Nothing happens on Wayland** — check the terminal output: Textr prints why capture or output failed. Likely causes: your user isn't in the `input` group (evdev capture), `python-evdev` isn't installed, or the `ydotool` daemon isn't running.
- **Paste does nothing in a terminal** — some terminals expect a different paste shortcut than `Ctrl+Shift+V`. Set it via **Settings → Paste shortcut** in the UI.
- **Input capture broken on Wayland** — run `python3 -m diag` (or `textr-diag` after installing) for a report on session type, device readability, missing packages, and ydotool; add `--listen 5` to press keys and see the keycode → character mapping under your layout.
- **Listener errors** — the daemon logs exceptions to the terminal and keeps running; if you see repeated `exception in key handler` tracebacks, check recently edited snippets for missing fields.
