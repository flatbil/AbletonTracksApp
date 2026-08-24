#!/usr/bin/env bash
# update-bridge.sh — deploy bridge code changes to the installed location and restart.
# Run from the project directory after pulling or editing bridge code:
#   bash update-bridge.sh

set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BRIDGE_SUPPORT="$HOME/Library/Application Support/StagePadBridge"

if [ ! -d "$BRIDGE_SUPPORT" ]; then
    echo "Bridge not installed. Run install.sh first."
    exit 1
fi

echo "Syncing bridge code to $BRIDGE_SUPPORT..."
rsync -a --delete "$SCRIPT_DIR/bridge/" "$BRIDGE_SUPPORT/bridge/"
echo "  OK"

REMOTE_SCRIPTS_DIR="$HOME/Music/Ableton/User Library/Remote Scripts"
ABLETONOSC_DEST="$REMOTE_SCRIPTS_DIR/AbletonOSC"
if [ -d "$ABLETONOSC_DEST" ]; then
    echo "Syncing AbletonOSC remote script to $ABLETONOSC_DEST..."
    # --exclude logs/ preserves any existing log file in place rather than
    # deleting it (the rotating handler will cap it going forward regardless).
    rsync -a --delete --exclude 'logs/' "$SCRIPT_DIR/AbletonOSC/" "$ABLETONOSC_DEST/"
    echo "  OK — restart Ableton (or toggle the AbletonOSC Control Surface off/on) to load the update."
fi

echo "Restarting bridge service..."
launchctl stop com.nuthouse.stagepad-bridge
sleep 1
launchctl start com.nuthouse.stagepad-bridge
echo "  OK"

echo ""
echo "Done. Tail logs with:"
echo "  tail -f ~/Library/Logs/StagePadBridge/bridge.log"
