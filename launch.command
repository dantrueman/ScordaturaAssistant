#!/bin/bash
cd "$(dirname "$0")"

if [ ! -d ".venv" ]; then
    echo "Setting up virtual environment..."
    if ! python3 -m venv .venv; then
        echo ""
        echo "ERROR: Could not create virtual environment."
        echo "If this folder is in Downloads, move it to Applications or Documents and try again."
        echo "macOS restricts certain operations in the Downloads folder."
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
