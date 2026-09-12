#!/bin/bash
cd "$(dirname "$0")"

VENV="$HOME/.scordatura/venv"

if [ ! -d "$VENV" ]; then
    echo "Setting up virtual environment..."
    mkdir -p "$HOME/.scordatura"
    if ! python3 -m venv "$VENV"; then
        echo ""
        echo "ERROR: Could not create virtual environment."
        echo "Please make sure Python 3.9 or later is installed:"
        echo "https://www.python.org/downloads/"
        echo ""
        read -rp "Press Enter to close..."
        exit 1
    fi
    echo "Installing dependencies (this may take a minute)..."
    if ! "$VENV/bin/python3" -m pip install -r requirements.txt; then
        echo ""
        echo "ERROR: Could not install dependencies."
        echo "Check your internet connection and try again."
        echo ""
        read -rp "Press Enter to close..."
        exit 1
    fi
fi

"$VENV/bin/python" -m scordatura.web
