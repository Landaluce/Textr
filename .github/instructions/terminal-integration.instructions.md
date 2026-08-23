---
name: terminal-integration-instructions
description: "Use when: working with daemon background processes, terminal startup scripts, managing the text expansion service from command line, debugging keyboard listener or snippet expansion issues"
applyTo: "daemon.py,main.py,run.sh"
---

# Terminal Integration Guide for Texter MVP

## Running from Terminal

### Basic Startup
```bash
# Using the provided shell script
bash run.sh

# Direct Python execution
python3 main.py

# With explicit output/logging to file
python3 main.py > texter.log 2>&1 &
```

### Background Execution
To run as a background daemon and allow terminal use:
```bash
# Run in background with disown (survives terminal close)
python3 main.py &
disown

# Or with nohup
nohup python3 main.py &
```

## Daemon Monitoring

### Check if Process is Running
```bash
ps aux | grep "python3 main.py"
ps aux | grep texter
```

### Terminate the Service
```bash
# Find and kill the process
pkill -f "python3 main.py"
killall python3  # (use with caution)

# Kill by PID
kill <PID>
kill -9 <PID>  # force kill if necessary
```

## Terminal Output & Debugging

### Enable Debug Output
The daemon respects a `debug` flag. When troubleshooting:

1. Modify `daemon.py` to set `self.debug = True`
2. Watch terminal for keyboard listener events and expansion operations
3. Look for file I/O errors when loading config.json or snippets.json

### Log Common Issues
- **Keyboard listener errors**: Permission issues on Linux/Mac with keyboard input monitoring
- **File not found**: snippets.json or config.json not in project root
- **PySide6 display errors**: Missing DISPLAY on headless systems

## Environment Setup via Terminal

### Install Dependencies
```bash
# Using pip
pip install -r requirements.txt

# Using pipenv
pipenv install --dev

# Verify installation
python3 -c "import PySide6, pynput, pyperclip; print('All dependencies installed')"
```

### Environment Variables
- `DISPLAY`: Required for PySide6 GUI on headless systems
- `PYTHONUNBUFFERED=1`: Helpful for real-time terminal output during debugging

## Integration with Terminal Applications

### Calling Daemon Methods from CLI
```bash
# Load and test snippet expansion
python3 -c "
from daemon import Daemon
d = Daemon()
print('Daemon initialized')
print(f'Snippets loaded: {len(d.snippets)}')
"

# Check configuration
python3 -c "from daemon import Daemon; d = Daemon(); print(d.config)"
```

### Testing Keyboard Input
```bash
# Test if pynput can capture keyboard (requires running process)
python3 -c "
from pynput import keyboard

def on_press(key):
    try:
        print(f'Key pressed: {key.char}')
    except AttributeError:
        print(f'Special key pressed: {key}')

listener = keyboard.Listener(on_press=on_press)
listener.start()
print('Listening to keyboard... Press Ctrl+C to stop')
listener.join()
"
```

## Common Terminal Commands in Development

| Task | Command |
|------|---------|
| Run application | `python3 main.py` |
| Run in background | `python3 main.py &` |
| Stop application | `pkill -f "python3 main.py"` |
| View recent logs | `tail -f texter.log` |
| Test daemon setup | `python3 -c "from daemon import Daemon; Daemon()"` |
| Check dependencies | `pip freeze \| grep -E "PySide6\|pynput\|pyperclip"` |
| Format code | `python3 -m black *.py` (if installed) |

## Important Notes for Terminal Integration

- **Keyboard monitoring requires privileges**: On some Linux systems, you may need to run with sudo or add the user to input groups
- **Clipboard access**: pyperclip needs access to system clipboard (may fail in headless environments)
- **Signal handling**: The daemon catches keyboard.Listener exit; ensure proper cleanup on Ctrl+C
- **Config persistence**: All changes are written to `config.json` and `snippets.json` - watch for file permission issues

## Troubleshooting Terminal Issues

### "No module named PySide6"
```bash
pip install PySide6>=6.6
python3 -m pip install --upgrade pip
```

### "Cannot connect to X server"
- Application requires display. Either:
  - Run with `export DISPLAY=:0` if X11 is available
  - Or remove UI dependency for daemon-only mode

### "Permission denied" on keyboard input
```bash
# Linux: Add user to input group
sudo usermod -a -G input $USER
# Requires logout/login to take effect
```

### Application exits immediately
- Check `texter.log` for exceptions
- Run with explicit Python error output: `python3 -u main.py`
- Test daemon initialization: `python3 -c "from daemon import Daemon; print('OK')"`
