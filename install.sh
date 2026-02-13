#!/bin/bash
# ─────────────────────────────────────────────────────────────────
# Soniox Transcript Converter — Installer
#
# Run this once to set up the app:
#   1. Open Terminal
#   2. Drag this file into the Terminal window and press Enter
#      — or —
#      cd to this folder and run:  bash install.sh
# ─────────────────────────────────────────────────────────────────

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo ""
echo "╔══════════════════════════════════════════════╗"
echo "║   Soniox Transcript Converter — Installer    ║"
echo "╚══════════════════════════════════════════════╝"
echo ""

# ── Check Python ──────────────────────────────────────────────────
if ! command -v python3 &>/dev/null; then
    echo "ERROR: python3 not found."
    echo "Install it from https://www.python.org/downloads/ or via:"
    echo "  xcode-select --install"
    exit 1
fi

PYVER=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
echo "Found Python $PYVER"

# ── Create virtual environment ────────────────────────────────────
if [ ! -d ".venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv .venv
    echo "Virtual environment created."
else
    echo "Virtual environment already exists."
fi

# ── Install dependencies ──────────────────────────────────────────
echo "Installing dependencies..."
.venv/bin/pip install --upgrade pip --quiet
.venv/bin/pip install httpx python-dotenv jsonschema --quiet
echo "Dependencies installed."

# ── Set up API key ────────────────────────────────────────────────
if [ ! -f ".env" ]; then
    echo ""
    echo "─── API Key Setup ───"
    echo "You need a Soniox API key to use this app."
    echo "Get one at: https://soniox.com"
    echo ""
    read -p "Paste your Soniox API key (or press Enter to skip): " API_KEY
    if [ -n "$API_KEY" ]; then
        echo "SONIOX_API_KEY=$API_KEY" > .env
        echo "API key saved to .env"
    else
        echo "Skipped. You can add it later by creating a .env file with:"
        echo "  SONIOX_API_KEY=your_key_here"
    fi
else
    echo "API key file (.env) already exists."
fi

# ── Make launcher executable ──────────────────────────────────────
if [ -f "Soniox Converter.command" ]; then
    chmod +x "Soniox Converter.command"
    echo "Launcher is ready."
fi

echo ""
echo "════════════════════════════════════════════════"
echo "  Installation complete!"
echo ""
echo "  To launch the app:"
echo "    Double-click 'Soniox Converter.command'"
echo "    — or —"
echo "    Run: bash \"Soniox Converter.command\""
echo "════════════════════════════════════════════════"
echo ""
