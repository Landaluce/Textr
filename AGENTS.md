---
name: texter-mvp-agent
description: "AI agent instructions for the Texter MVP project - a Python-based text expansion tool with daemon and UI components"
---

# Texter MVP - Agent Instructions

## Project Overview

**Texter MVP** is a Python application that provides text snippet expansion via keyboard monitoring. It consists of:
- **Daemon** (`daemon.py`): Background process that monitors keyboard input and expands snippets
- **UI** (`ui.py`): PySide6-based GUI with system tray integration for managing snippets and settings; the snippet list is a `QTreeWidget` (`SnippetTreeWidget`) that groups by category folders/type/tag with search and sorting
- **Core** (`main.py`): Application entry point that initializes the UI

## Quick Start Commands

| Command | Purpose |
|---------|---------|
| `bash run.sh` or `python3 main.py` | Start the application from the source tree (launches daemon + UI) |
| `python3 -m pip install .` then `textr` | Install as a package and start it (console entry point) |
| `python3 -m diag` or `textr-diag` | Environment diagnostics: session type, input devices, tool/permission checks (add `--listen 5` for live keycode capture) |
| `python3 -c "from daemon import Daemon; d = Daemon(); d.load_snippets()"` | Test daemon snippet loading |
| `python3 -m pip install -r requirements.txt` | Install dependencies (PySide6, pynput, pyperclip, evdev) |

## Architecture & Key Files

### daemon.py
- **Responsibility**: Background keyboard monitoring and snippet expansion
- **Key methods**: 
  - `load_snippets()`: Loads snippet definitions from `snippets.json`
  - `load_config()`: Loads user settings from `config.json`
  - `save_config()`: Persists configuration changes
- **Paste shortcut**: clipboard-mode expansion uses `config["paste_hotkey"]` (default `ctrl+shift+v`), parsed by `_paste_hotkey_parts()` into modifier keys + final key; configurable in the UI settings
- **Snippet Types**: `variable` (static text), `template` (language-specific code), `command` (actions like toggle_pause)
- **Input backends**: pynput (X11/XWayland) and `input_wayland.py` (evdev, native Wayland) both emit normalized `keys.Key` objects (`name`/`char`) consumed by the backend-agnostic `_handle_key`
- **Keyboard layouts**: `input_wayland.py` maps evdev keycodes through `LAYOUTS` (`us`/`uk`/`de`/`fr`/`es`, each entry `(level1, level2, altgr)`); the active layout comes from `config["layout"]` and is selectable in the UI settings
- **Prefix disambiguation**: when a trigger is the prefix of another (`:d` vs `:dt`), `_handle_key` defers expansion via `pending_trigger` until the next key disambiguates (longer trigger, boundary key/char fires the short one, or abort); disabled snippets (`"enabled": false`) are excluded from matching AND deferral via `_enabled()`
- **Date/time formats**: `get_context()` and the `:d`/`:time`/`:datetime` command expander honor `config["date_format"]` / `config["time_format"]` (strftime strings, defaults `%Y-%m-%d` / `%H:%M`)
- **Snippet grouping/tags/usage**: snippets carry an optional `tags` list (legacy `category` is normalized into `tags` on load); the daemon records `usage`/`last_used` on every expansion (`_record_usage`) for the UI's recently/most-used sorting
- **Data paths**: `_resolve_path()` prefers `$TEXTR_CONFIG_DIR`, then a legacy CWD file, then `$XDG_CONFIG_HOME/textr/`

### ui.py
- **Responsibility**: QApplication GUI with system tray icon
- **Key components**:
  - `TexterUI`: Main window with snippet management, category filtering, and configuration
  - `AddSnippetDialog`: Modal for creating/editing snippets
  - System tray integration for minimize/restore
- **Settings UI**: Configure name, email, keyboard layout, paste shortcut, date/time formats, writing mode, debug verbosity
- **Trigger conflict warnings**: `TexterUI.trigger_conflicts()` flags duplicate triggers and prefix-deferral collisions when adding/editing snippets (`_confirm_trigger_conflicts` warns before saving)
- **Import safety**: `import_json()` normalizes legacy list format to the dict format, warns (via a confirm dialog) about triggers it will overwrite, then imports
- **Per-snippet disable**: `AddSnippetDialog` has an Enabled checkbox (writes `"enabled": false` when unchecked); disabled snippets render dimmed (`DISABLED_COLOR`) in the tree and the preview notes the disabled state
- **No status label**: the status bar was removed; the toggle button text + tray tooltip carry daemon/writing state, and transient feedback (saved/added/imported) goes to the terminal via `print` - keep print-based feedback when changing these flows
- **Quit paths**: the window's X button (via `closeEvent`), the Quit button, and the tray Quit action all route through `quit_app()` (saves snippets, stops daemon, hides tray) - keep them unified when changing quit behavior
- **Manager shortcuts**: `Ctrl+N` is a window-wide `QShortcut`; `F2`/`Enter` (edit) and `Del` (delete) use `Qt.WidgetWithChildrenShortcut` scoped to `snippet_list` so the search box never triggers them - keep this scoping when adding shortcuts

### Configuration Files
- **config.json**: User settings (name, email, custom options) - loaded at startup, persisted on save
- **snippets.json**: Snippet definitions - structure: `[{"trigger": "xx", "type": "variable", "value": "...", "category": "..."}]`
- **Location**: resolved by `Daemon._resolve_path()` to `$XDG_CONFIG_HOME/textr/` (default `~/.config/textr/`) with a legacy fallback to files next to the working directory; `$TEXTR_CONFIG_DIR` overrides everything (used by tests to stay hermetic)

## Common Development Tasks

### Adding Daemon Features
When modifying `daemon.py`:
- Import required modules at top (keyboard, pyperclip, json, etc. already imported)
- Update `__init__()` to initialize new state variables
- Add corresponding load/save logic to `load_config()` / `save_config()` for persistence
- Keep key handling backend-agnostic: work with normalized keys (`key.name`/`key.char`, see `keys.py`) rather than pynput key objects, and extend both backends if behavior must differ by platform
- Test with: `python3 -c "from daemon import Daemon; d = Daemon()"`

### Modifying UI/Settings
When working on `ui.py`:
- QSS stylesheets use Catppuccin Mocha theme (dark mode, purples/blues)
- Form layouts use QFormLayout for consistency
- Signal/slot connections for UI updates and user interactions
- Always validate user input before passing to daemon

### Managing Snippets
Snippet JSON structure:
```json
{
  "trigger": "shorthand",
  "type": "variable|template|command",
  "category": "python",
  "value": "expansion text",
  "languages": {"python": "def f(): pass", "js": "function f() {}"},
  "action": "set_language|toggle_pause|shutdown"
}
```

## Terminal Integration Points

### Background Daemon Execution
- The daemon runs continuously during application lifecycle
- Monitor logs for keyboard hook issues or expansion failures
- Use `self.debug` flag in Daemon to enable verbose output to terminal

### Configuration Persistence
- Changes made in UI are persisted to `config.json` on save
- Snippets are persisted to `snippets.json` on add/edit/delete
- File I/O errors are caught and logged to terminal

### Dependencies & Environment
- **Python 3.9+** (Pipfile specifies 3.9)
- **Key packages**: PySide6 (UI framework), pynput (X11 keyboard monitoring), pyperclip (clipboard), evdev (Wayland keyboard capture, Linux only)
- **Packaging**: `pyproject.toml` exposes the `textr` console script; adding a module requires listing it in `[tool.setuptools] py-modules`
- **Development**: ipykernel available in Pipfile for interactive environments

## Debugging & Common Issues

### Daemon Not Expanding Snippets
1. Check `snippets.json` is valid JSON and in correct location (see `_resolve_path` — it may now live in `~/.config/textr/`)
2. Verify keyboard listener is active: on X11 the daemon has `self.listener` and `self.hotkey_listener`; on Wayland `self.wayland_input` (evdev) must be running — check the startup messages for permission errors
3. Check debug output: ensure snippet trigger matches exactly
4. With both `:d` and `:dt` defined, `:d` will not fire until a boundary key/char follows (prefix disambiguation) — expected

### UI Not Responding
1. Ensure PySide6 event loop is running (happens in `main.py`)
2. Check for blocking operations in keyboard listener callbacks
3. Verify system tray integration hasn't been disabled

## Agent Guidelines

When working in this codebase:
- **Always test daemon changes** with quick Python REPL validation
- **Preserve QSS stylesheet** consistency when modifying UI components
- **File paths** are relative to project root (config.json, snippets.json)
- **Terminal output** from daemon is important for debugging - preserve logging statements
- **New dependencies** require updating both `requirements.txt` and `Pipfile`
