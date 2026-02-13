#!/bin/bash
# ─────────────────────────────────────────────────────────────────
# Soniox Transcript Converter — Installer
# Double-click this file to install. No terminal knowledge needed.
# ─────────────────────────────────────────────────────────────────

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# ── Helper: show macOS dialog ─────────────────────────────────────
dialog() {
    osascript -e "display dialog \"$1\" with title \"Soniox Converter\" buttons {\"OK\"} default button \"OK\" $2" 2>/dev/null
}

ask_yes_no() {
    osascript -e "display dialog \"$1\" with title \"Soniox Converter\" buttons {\"No\", \"Yes\"} default button \"Yes\"" -e 'button returned of result' 2>/dev/null
}

# ── Find a Python with working Tkinter ──────────────────────────
# macOS system Python (3.9) ships Tcl/Tk 8.5 which is broken on
# macOS 13+. We need Homebrew or python.org Python with modern Tk.
find_good_python() {
    # 1. Check Homebrew pythons (newest first)
    for ver in 3.14 3.13 3.12 3.11; do
        local py="/opt/homebrew/bin/python${ver}"
        if [ -x "$py" ] && "$py" -c "import tkinter" 2>/dev/null; then
            echo "$py"
            return 0
        fi
    done
    # 2. Check python.org framework installs
    for ver in 3.14 3.13 3.12 3.11; do
        local py="/Library/Frameworks/Python.framework/Versions/${ver}/bin/python3"
        if [ -x "$py" ] && "$py" -c "import tkinter" 2>/dev/null; then
            echo "$py"
            return 0
        fi
    done
    # 3. Check generic python3 if it has working Tk
    if command -v python3 &>/dev/null; then
        if python3 -c "import tkinter; tkinter.Tk(); exit()" 2>/dev/null; then
            echo "python3"
            return 0
        fi
    fi
    return 1
}

PYTHON=$(find_good_python)

if [ -z "$PYTHON" ]; then
    # No working Python+Tk found — try to install via Homebrew
    if command -v brew &>/dev/null; then
        INSTALL=$(ask_yes_no "Soniox Converter needs a modern Python with Tkinter.\n\nThe macOS built-in Python's Tkinter is broken on this macOS version.\n\nInstall Python 3.13 via Homebrew now?")
        if [ "$INSTALL" = "Yes" ]; then
            echo "Installing Python 3.13 via Homebrew..."
            brew install python@3.13 python-tk@3.13 2>&1
            PYTHON=$(find_good_python)
        fi
    fi

    if [ -z "$PYTHON" ]; then
        dialog "A modern Python with Tkinter is required.\n\nOption 1: Install Homebrew (https://brew.sh),\nthen run this installer again.\n\nOption 2: Install Python from https://www.python.org/downloads/" "with icon stop"
        exit 1
    fi
fi

PYVER=$("$PYTHON" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
echo "Using Python $PYVER at $PYTHON"

# ── Create virtual environment ────────────────────────────────────
if [ -d ".venv" ]; then
    # Check if existing venv has working tkinter
    if ! .venv/bin/python3 -c "import tkinter" 2>/dev/null; then
        echo "Existing venv has broken Tkinter, recreating..."
        rm -rf .venv
    fi
fi

if [ ! -d ".venv" ]; then
    echo "Creating virtual environment..."
    "$PYTHON" -m venv .venv
    if [ $? -ne 0 ]; then
        dialog "Failed to create virtual environment.\n\nMake sure Python $PYVER is properly installed." "with icon stop"
        exit 1
    fi
fi

# ── Install dependencies ──────────────────────────────────────────
echo "Installing dependencies..."
.venv/bin/pip install --upgrade pip --quiet 2>&1
.venv/bin/pip install httpx python-dotenv jsonschema --quiet 2>&1
if [ $? -ne 0 ]; then
    dialog "Failed to install dependencies.\n\nCheck your internet connection and try again." "with icon stop"
    exit 1
fi

# ── Set up API key ────────────────────────────────────────────────
if [ ! -f ".env" ] || ! grep -q "SONIOX_API_KEY" .env 2>/dev/null; then
    API_KEY=$(osascript -e '
        display dialog "Enter your Soniox API key.\n\nGet one at: https://soniox.com" with title "Soniox Converter — API Key Setup" default answer "" buttons {"Skip for now", "Save"} default button "Save"
    ' -e 'text returned of result' 2>/dev/null)

    if [ -n "$API_KEY" ]; then
        echo "SONIOX_API_KEY=$API_KEY" > .env
    fi
fi

# ── Make launcher executable ──────────────────────────────────────
chmod +x "$SCRIPT_DIR/Soniox Converter.command" 2>/dev/null

# ── Done — offer to launch ───────────────────────────────────────
LAUNCH=$(ask_yes_no "Installation complete!\n\nLaunch Soniox Converter now?")
if [ "$LAUNCH" = "Yes" ]; then
    open "$SCRIPT_DIR/Soniox Converter.command"
fi
