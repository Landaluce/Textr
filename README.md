# Textr

Text expansion for Linux (X11). Type a short trigger anywhere and Textr expands it into full text, code templates, or actions — with a PySide6 GUI for managing snippets and settings.

## Features

- **Global trigger detection** — works in any application via a background keyboard daemon
- **Three snippet types** — static text (`variable`), per-language code (`template`), and actions (`command`)
- **Two insertion modes** — fast clipboard paste (default) or human-like typing ("Human Typing")
- **Word-boundary matching** — triggers only fire after whitespace/punctuation, never mid-word
- **Template variables** — `{name}`, `{email}`, `{date}`, `{time}`, `{clipboard}`
- **Global hotkey** — `Ctrl+Shift+P` toggles pause/resume
- **Crash resilience** — the keyboard listener survives internal errors and restarts automatically instead of silently dying

## Requirements

- Linux with X11 (Wayland is not supported by the keyboard backend)
- Python 3.9+

```txt
PySide6>=6.6
pynput>=1.7
pyperclip>=1.9
```

## Installation

```bash
python3 -m pip install -r requirements.txt
```

## Usage

```bash
bash run.sh          # or: python3 main.py
```

This starts both the UI window and the background daemon. Close or hide the window; triggers keep working while it is hidden.

### Snippet types

| Type | Behavior |
| ------ | ---------- |
| `variable` | Expands to static text, e.g. trigger `sig` → your signature |
| `template` | Expands to language-specific code chosen by the current language selector |
| `command`  | Performs an action (no text inserted), e.g. `:d` inserts today's date |

Snippet JSON structure:

```json
{
  "trigger": "sig",
  "type": "variable",
  "category": "personal",
  "value": "Best, {name} ({email})"
}
```

Templates use `{"python": "...", "javascript": "..."}` under `"languages"` and support the same `{variable}` substitution.

### Built-in commands

Commands are just snippets of type `command` with an `action` field:

| Action | Effect |
|--------|--------|
| `toggle_pause` / `resume` | Pause or resume expansion |
| `set_language` | Switch the template language (`value` holds the language) |
| `date` / `time` / `datetime` | Insert the current date/time |
| `reload` | Re-read `snippets.json` from disk |
| `save` | Persist snippets and config |
| `ui` / `top` / `hide` | Show, raise, or hide the manager window |
| `restart` / `shutdown` | Restart or quit the app |

## Configuration & data files

Both files live next to the source and are regenerated if missing:

- `config.json` — user profile (`name`, `email`)
- `snippets.json` — all snippet definitions

They are personal data and are excluded from version control via `.gitignore`.

## Development

```bash
python3 -m pytest tests/
```

The test suite exercises the daemon without X11: the keyboard controller, clipboard, and sleeps are faked, so it runs headless in CI.

### Architecture

```
main.py ── starts ──> TexterUI (Qt GUI thread)
                  └─> Daemon.run() on a worker thread
                        ├─ pynput Listener  -> on_press -> buffer + trigger match
                        └─ pynput GlobalHotKeys (Ctrl+Shift+P)
```

Threading rules that keep the daemon alive:

- The GUI thread and the keyboard listener thread share `daemon.snippets`; every access goes through `snippets_lock`, and UI mutations go through the helper methods (`add_or_update_snippet`, `replace_snippet`, `delete_snippet`, `import_snippets`).
- Nothing inside the key handler may touch Qt widgets or raise — UI actions requested by command snippets are delivered to the GUI thread via the `daemon_command` Signal.

## Troubleshooting

- **Triggers not expanding** — the daemon may be paused: press `Ctrl+Shift+P` once, or check the Daemon button/status label in the Texter window.
- **Paste does nothing in a terminal** — some terminals expect a different paste shortcut than `Ctrl+Shift+V`.
- **Listener errors** — the daemon logs exceptions to the terminal and keeps running; if you see repeated `exception in key handler` tracebacks, check recently edited snippets for missing fields.
