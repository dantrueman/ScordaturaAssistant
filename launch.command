#!/bin/bash
APP_DIR="$(dirname "$0")"

VENV="$HOME/.scordatura/venv"

# Create venv if it doesn't exist
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
fi

# Install/update packages if scordatura is not installed in the venv
if ! "$VENV/bin/python3" -c "import scordatura" 2>/dev/null; then
    echo "Installing dependencies (this may take a minute)..."
    cd "$HOME"
    if ! "$VENV/bin/python3" -m pip install "$APP_DIR"; then
        echo ""
        echo "ERROR: Could not install dependencies."
        echo "Check your internet connection and try again."
        echo ""
        read -rp "Press Enter to close..."
        exit 1
    fi
fi

cd "$HOME"
"$VENV/bin/python3" -m scordatura.web
