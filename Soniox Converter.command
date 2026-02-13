#!/bin/bash
# ─────────────────────────────────────────────────────────────────
# Soniox Transcript Converter — GUI Launcher
# Double-click this file to launch the app.
# If not installed yet, it will install automatically.
# ─────────────────────────────────────────────────────────────────

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# ── Auto-install if needed ────────────────────────────────────────
if [ ! -d ".venv" ]; then
    if [ -f "$SCRIPT_DIR/Install Soniox Converter.command" ]; then
        bash "$SCRIPT_DIR/Install Soniox Converter.command"
        [ ! -d ".venv" ] && exit 1
    else
        osascript -e 'display dialog "Soniox Converter is not set up yet.\n\nPlease run the installer first." with title "Soniox Converter" buttons {"OK"} default button "OK" with icon caution' 2>/dev/null
        exit 1
    fi
fi

# ── Check API key ─────────────────────────────────────────────────
if [ ! -f ".env" ] || ! grep -q "SONIOX_API_KEY" .env 2>/dev/null; then
    API_KEY=$(osascript -e '
        display dialog "Enter your Soniox API key to get started.\n\nGet one at: https://soniox.com" with title "Soniox Converter — API Key" default answer "" buttons {"Cancel", "Save"} default button "Save"
    ' -e 'text returned of result' 2>/dev/null)

    if [ -n "$API_KEY" ]; then
        echo "SONIOX_API_KEY=$API_KEY" > .env
    else
        osascript -e 'display dialog "No API key provided.\n\nThe app needs a Soniox API key to transcribe audio.\nGet one at: https://soniox.com" with title "Soniox Converter" buttons {"OK"} default button "OK" with icon caution' 2>/dev/null
        exit 1
    fi
fi

# ── Load env ──────────────────────────────────────────────────────
export $(grep -v '^#' .env | xargs)

# ── Launch GUI via Python.app framework bundle ────────────────────
# Tkinter on macOS crashes (SIGABRT in TkpInit) if the Python process
# doesn't have proper GUI/foreground app status. The system Python.app
# bundle has the correct Info.plist for macOS to treat it as a GUI app.
# We set PYTHONPATH so it finds both the project code and venv packages.
VENV_SITE="$SCRIPT_DIR/.venv/lib/python3.9/site-packages"
FRAMEWORK_PYTHON="/Library/Developer/CommandLineTools/Library/Frameworks/Python3.framework/Versions/3.9/Resources/Python.app/Contents/MacOS/Python"

if [ -f "$FRAMEWORK_PYTHON" ]; then
    export PYTHONPATH="$SCRIPT_DIR:$VENV_SITE${PYTHONPATH:+:$PYTHONPATH}"
    exec "$FRAMEWORK_PYTHON" -m soniox_converter.gui
else
    # Fallback for non-CommandLineTools Python installs
    exec .venv/bin/python3 -m soniox_converter.gui
fi
