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

# ── Check Python ──────────────────────────────────────────────────
if ! command -v python3 &>/dev/null; then
    dialog "Python 3 is required but not installed.\n\nPlease install it from:\nhttps://www.python.org/downloads/\n\nOr run this in Terminal:\nxcode-select --install" "with icon stop"
    exit 1
fi

PYVER=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")

# ── Create virtual environment ────────────────────────────────────
if [ ! -d ".venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv .venv
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
