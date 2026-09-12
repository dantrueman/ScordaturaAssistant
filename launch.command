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

# Install packages if scordatura is not yet available in the venv
if ! "$VENV/bin/python3" -c "import scordatura" 2>/dev/null; then
    echo "Installing dependencies (this may take a minute)..."
    cd "$HOME"

    # Upgrade pip, then install external dependencies from PyPI
    "$VENV/bin/python3" -m pip install --upgrade pip --quiet
    if ! "$VENV/bin/python3" -m pip install "music21>=8.3.0" "flask>=2.3"; then
        echo ""
        echo "ERROR: Could not install dependencies."
        echo "Check your internet connection and try again."
        echo ""
        read -rp "Press Enter to close..."
        exit 1
    fi

    # Copy the scordatura package directly into site-packages
    python3_ver="$("$VENV/bin/python3" -c 'import sys; print(f"python{sys.version_info.major}.{sys.version_info.minor}")')"
    site_packages="$VENV/lib/$python3_ver/site-packages"
    rm -rf "$site_packages/scordatura"
    if ! cp -r "$APP_DIR/scordatura" "$site_packages/"; then
        echo ""
        echo "ERROR: Could not copy scordatura package."
        echo ""
        read -rp "Press Enter to close..."
        exit 1
    fi
fi

cd "$HOME"
"$VENV/bin/python3" -m scordatura.web
