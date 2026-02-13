#!/bin/bash
# ─────────────────────────────────────────────────────────────────
# Soniox Transcript Converter — GUI Launcher
#
# Double-click this file to launch the app.
# On first run, macOS may ask you to allow it in
# System Preferences → Privacy & Security.
# ─────────────────────────────────────────────────────────────────

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# ── Check installation ────────────────────────────────────────────
if [ ! -d ".venv" ]; then
    osascript -e 'display dialog "Soniox Converter is not installed yet.\n\nPlease run install.sh first:\n1. Open Terminal\n2. Drag install.sh into the window\n3. Press Enter" with title "Soniox Converter" buttons {"OK"} default button "OK" with icon caution'
    exit 1
fi

# ── Check API key ─────────────────────────────────────────────────
if [ ! -f ".env" ] || ! grep -q "SONIOX_API_KEY" .env 2>/dev/null; then
    API_KEY=$(osascript -e 'display dialog "Enter your Soniox API key:" with title "Soniox Converter — API Key" default answer "" buttons {"Cancel", "Save"} default button "Save"' -e 'text returned of result' 2>/dev/null)
    if [ -n "$API_KEY" ]; then
        echo "SONIOX_API_KEY=$API_KEY" > .env
    else
        osascript -e 'display dialog "No API key provided. The app needs a Soniox API key to work.\n\nGet one at https://soniox.com" with title "Soniox Converter" buttons {"OK"} default button "OK" with icon caution'
        exit 1
    fi
fi

# ── Load env and launch ──────────────────────────────────────────
export $(grep -v '^#' .env | xargs)
.venv/bin/python3 -m soniox_converter.gui
