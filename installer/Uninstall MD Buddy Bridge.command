#!/bin/bash
# MD Buddy Bridge Uninstaller
# Double-click this file to uninstall.

PLIST_NAME="com.nuthouse.stagepad-bridge"
PLIST_PATH="$HOME/Library/LaunchAgents/$PLIST_NAME.plist"
LOG_DIR="$HOME/Library/Logs/MDBuddyBridge"
BRIDGE_APP="/Library/Application Support/MDBuddyBridge"
ABLETON_OSC="$HOME/Music/Ableton/User Library/Remote Scripts/AbletonOSC"

echo "╔════════════════════════════════════════╗"
echo "║   MD Buddy Bridge Uninstaller          ║"
echo "╚════════════════════════════════════════╝"
echo ""

# ── Stop the running service ──────────────────────────────────────────────────
if launchctl list 2>/dev/null | grep -q "$PLIST_NAME"; then
    echo "Stopping bridge service..."
    launchctl bootout "gui/$(id -u)/$PLIST_NAME" 2>/dev/null || \
    launchctl unload "$PLIST_PATH" 2>/dev/null || true
    sleep 1
fi

# ── Remove launchd plist ──────────────────────────────────────────────────────
if [ -f "$PLIST_PATH" ]; then
    rm -f "$PLIST_PATH"
    echo "Removed: launchd plist"
fi

# ── Remove bridge application (requires sudo) ─────────────────────────────────
if [ -d "$BRIDGE_APP" ]; then
    echo "Removing bridge application (requires your password)..."
    sudo rm -rf "$BRIDGE_APP"
    echo "Removed: bridge application"
fi

# ── Remove log files ──────────────────────────────────────────────────────────
if [ -d "$LOG_DIR" ]; then
    rm -rf "$LOG_DIR"
    echo "Removed: log files"
fi

# ── Optionally remove AbletonOSC ──────────────────────────────────────────────
echo ""
if [ -d "$ABLETON_OSC" ]; then
    read -r -p "Also remove AbletonOSC from your Ableton User Library? [y/N] " confirm
    if [[ "$confirm" =~ ^[Yy]$ ]]; then
        rm -rf "$ABLETON_OSC"
        echo "Removed: AbletonOSC remote script"
        echo "Remember to remove it from Ableton Preferences → Control Surfaces."
    fi
fi

echo ""
echo "✓ MD Buddy Bridge has been uninstalled."
echo ""
echo "Press any key to close..."
read -r -n 1
