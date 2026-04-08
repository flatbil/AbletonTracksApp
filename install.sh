#!/usr/bin/env bash
# install.sh — sets up AbletonTracksApp on a new Mac
# Run once from the project directory: bash install.sh

set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLIST_NAME="com.nuthouse.stagepad-bridge"
PLIST_DEST="$HOME/Library/LaunchAgents/$PLIST_NAME.plist"

echo "=== AbletonTracksApp Installer ==="
echo ""

# ── 1. Ensure submodule is present ───────────────────────────────────────────
echo "[1/4] Checking AbletonOSC submodule..."
git -C "$SCRIPT_DIR" submodule update --init --recursive
echo "      OK"

# ── 2. Install Python dependencies ───────────────────────────────────────────
echo "[2/4] Installing Python dependencies..."
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

echo "[3/4] Installing AbletonOSC remote script..."
mkdir -p "$REMOTE_SCRIPTS_DIR"

if [ -d "$DEST" ]; then
    echo "      Found existing install at: $DEST"
    read -r -p "      Overwrite? [y/N] " confirm
    if [[ "$confirm" =~ ^[Yy]$ ]]; then
        rm -rf "$DEST"
        cp -r "$SRC" "$DEST"
        echo "      Updated."
    else
        echo "      Skipped."
    fi
else
    cp -r "$SRC" "$DEST"
    echo "      Installed to: $DEST"
fi

# ── 4. Install bridge as a login service (auto-starts & auto-restarts) ────────
echo "[4/4] Installing bridge as a background login service..."

LOG_DIR="$HOME/Library/Logs/StagePadBridge"
SCRIPTS_DIR="$HOME/Library/Scripts"
BRIDGE_SUPPORT="$HOME/Library/Application Support/StagePadBridge"
mkdir -p "$LOG_DIR" "$SCRIPTS_DIR" "$BRIDGE_SUPPORT"

# Copy the bridge package to the internal SSD.
# macOS TCC blocks launchd agents from accessing external volumes, so the
# bridge code must live on the internal drive to auto-start at login.
echo "      Copying bridge package to internal SSD..."
cp -r "$SCRIPT_DIR/bridge" "$BRIDGE_SUPPORT/"

# Write the launcher script (also on internal SSD)
cat > "$SCRIPTS_DIR/start-stagepad-bridge.sh" << LAUNCHER
#!/bin/bash
exec >> "$LOG_DIR/bridge.log" 2>&1
echo "[launcher] Starting at \$(date)"
export PYTHONPATH="$BRIDGE_SUPPORT"
cd "\$HOME"
exec /usr/bin/python3 -m bridge.main
LAUNCHER
chmod +x "$SCRIPTS_DIR/start-stagepad-bridge.sh"

# Stamp the scripts dir and log path into the plist
sed -e "s|SCRIPTS_DIR_PLACEHOLDER|$SCRIPTS_DIR|g" \
    -e "s|LOG_DIR_PLACEHOLDER|$LOG_DIR|g" \
    "$SCRIPT_DIR/$PLIST_NAME.plist" > "$PLIST_DEST"

# Load it now (unload first in case it was already registered)
launchctl unload "$PLIST_DEST" 2>/dev/null || true
launchctl load "$PLIST_DEST"
echo "      Bridge service installed and started."
echo "      Logs: $LOG_DIR/bridge.log"

echo ""
echo "=== Installation complete ==="
echo ""
echo "The bridge now starts automatically at login and restarts if it crashes."
echo ""
echo "Next steps:"
echo "  1. Open Ableton Live"
echo "  2. Go to Preferences → Link/Tempo/MIDI → Control Surface"
echo "  3. Select 'AbletonOSC' in one of the Control Surface slots"
echo ""
echo "Useful commands:"
echo "  Stop bridge:    launchctl unload '$PLIST_DEST'"
echo "  Start bridge:   launchctl load '$PLIST_DEST'"
echo "  View logs:      tail -f '$HOME/Library/Logs/StagePadBridge/bridge.log'"
