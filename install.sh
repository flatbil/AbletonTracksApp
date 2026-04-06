#!/usr/bin/env bash
# install.sh — sets up AbletonTracksApp on a new Mac
# Run once from the project directory: bash install.sh

set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "=== AbletonTracksApp Installer ==="
echo ""

# ── 1. Ensure submodule is present ───────────────────────────────────────────
echo "[1/3] Checking AbletonOSC submodule..."
git -C "$SCRIPT_DIR" submodule update --init --recursive
echo "      OK"

# ── 2. Install Python dependencies ───────────────────────────────────────────
echo "[2/3] Installing Python dependencies..."
if command -v pip3 &>/dev/null; then
    pip3 install -r "$SCRIPT_DIR/requirements.txt"
elif command -v pip &>/dev/null; then
    pip install -r "$SCRIPT_DIR/requirements.txt"
else
    python3 -m pip install -r "$SCRIPT_DIR/requirements.txt"
fi
echo "      OK"

# ── 3. Copy AbletonOSC remote script into Ableton's User Library ─────────────
REMOTE_SCRIPTS_DIR="$HOME/Music/Ableton/User Library/Remote Scripts"
DEST="$REMOTE_SCRIPTS_DIR/AbletonOSC"
SRC="$SCRIPT_DIR/AbletonOSC"

echo "[3/3] Installing AbletonOSC remote script..."
mkdir -p "$REMOTE_SCRIPTS_DIR"

if [ -d "$DEST" ]; then
    echo "      Found existing install at: $DEST"
    read -r -p "      Overwrite? [y/N] " confirm
    if [[ "$confirm" =~ ^[Yy]$ ]]; then
        rm -rf "$DEST"
    else
        echo "      Skipped."
        echo ""
        echo "=== Done (AbletonOSC not updated) ==="
        exit 0
    fi
fi

cp -r "$SRC" "$DEST"
echo "      Installed to: $DEST"

echo ""
echo "=== Installation complete ==="
echo ""
echo "Next steps:"
echo "  1. Open Ableton Live"
echo "  2. Go to Preferences → Link/Tempo/MIDI → Control Surface"
echo "  3. Select 'AbletonOSC' in one of the Control Surface slots"
echo "  4. Run the bridge: python3 -m bridge.main"
