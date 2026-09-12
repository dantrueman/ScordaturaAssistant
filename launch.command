#!/bin/bash
cd "$(dirname "$0")"

if [ ! -d ".venv" ]; then
    echo "Setting up virtual environment..."
    if ! python3 -m venv .venv; then
        echo ""
        echo "ERROR: Could not create virtual environment."
        echo "macOS is blocking Terminal from writing to this folder."
        echo ""
        echo "To fix: open System Settings → Privacy & Security → Files and Folders"
        echo "and make sure Terminal has access to the folder where this app is stored."
        echo ""
        echo "Alternatively, try granting Terminal Full Disk Access under Privacy & Security."
        echo ""
        read -rp "Press Enter to close..."
        exit 1
    fi
    echo "Installing dependencies (this may take a minute)..."
    if ! .venv/bin/python3 -m pip install -r requirements.txt; then
        echo ""
        echo "ERROR: Could not install dependencies."
        echo "Check your internet connection and try again."
        echo ""
        read -rp "Press Enter to close..."
        exit 1
    fi
fi

.venv/bin/python -m scordatura.web
