#!/usr/bin/env bash
# start.sh — starts the Ableton bridge
# Make sure Ableton is open with AbletonOSC loaded first.

set -e
cd "$(dirname "${BASH_SOURCE[0]}")"
python3 -m bridge.main
