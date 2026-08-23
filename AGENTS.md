---
name: texter-mvp-agent
description: "AI agent instructions for the Texter MVP project - a Python-based text expansion tool with daemon and UI components"
---

# Texter MVP - Agent Instructions

## Project Overview

**Texter MVP** is a Python application that provides text snippet expansion via keyboard monitoring. It consists of:
- **Daemon** (`daemon.py`): Background process that monitors keyboard input and expands snippets
- **UI** (`ui.py`): PySide6-based GUI with system tray integration for managing snippets and settings
- **Core** (`main.py`): Application entry point that initializes the UI

## Quick Start Commands

| Command | Purpose |
|---------|---------|
| `bash run.sh` or `python3 main.py` | Start the application (launches daemon + UI) |
| `python3 -c "from daemon import Daemon; d = Daemon(); d.load_snippets()"` | Test daemon snippet loading |
| `python3 -m pip install -r requirements.txt` | Install dependencies (PySide6, pynput, pyperclip) |

## Architecture & Key Files

### daemon.py
- **Responsibility**: Background keyboard monitoring and snippet expansion
- **Key methods**: 
  - `load_snippets()`: Loads snippet definitions from `snippets.json`
  - `load_config()`: Loads user settings from `config.json`
  - `save_config()`: Persists configuration changes
- **Snippet Types**: `variable` (static text), `template` (language-specific code), `command` (actions like toggle_pause)

### ui.py
- **Responsibility**: QApplication GUI with system tray icon
- **Key components**:
  - `TexterUI`: Main window with snippet management, category filtering, and configuration
  - `AddSnippetDialog`: Modal for creating/editing snippets
  - System tray integration for minimize/restore
- **Settings UI**: Configure name, email, writing mode, debug verbosity

### Configuration Files
- **config.json**: User settings (name, email, custom options) - loaded at startup, persisted on save
- **snippets.json**: Snippet definitions - structure: `[{"trigger": "xx", "type": "variable", "value": "...", "category": "..."}]`

## Common Development Tasks

### Adding Daemon Features
When modifying `daemon.py`:
- Import required modules at top (keyboard, pyperclip, json, etc. already imported)
- Update `__init__()` to initialize new state variables
- Add corresponding load/save logic to `load_config()` / `save_config()` for persistence
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
- **Key packages**: PySide6 (UI framework), pynput (keyboard monitoring), pyperclip (clipboard)
- **Development**: ipykernel available in Pipfile for interactive environments

## Debugging & Common Issues

### Daemon Not Expanding Snippets
1. Check `snippets.json` is valid JSON and in correct location
2. Verify keyboard listener is active: daemon has `self.listener` and `self.hotkey_listener`
3. Check debug output: ensure snippet trigger matches exactly

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
