#!/bin/bash
# Analyze Guide Track — double-click in Finder to run.
# Auto-fetches Guide clip path from Ableton, falls back to manual entry.

BRIDGE_URL="http://127.0.0.1:8766"
TRACK_NAME="${1:-Guide}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "========================================="
echo "  StagePad — Guide Track Analyzer"
echo "========================================="
echo ""

# Check bridge is reachable
if ! curl -sf "$BRIDGE_URL/health" > /dev/null 2>&1; then
    echo "ERROR: Bridge is not running. Start it first."
    read -n 1 -p "Press any key to close..."
    exit 1
fi

# Try to auto-fetch path + arrangement position from Ableton
echo "Looking up '$TRACK_NAME' track in Ableton..."
clip_json=$(curl -s "$BRIDGE_URL/guide_clip_path?track=$TRACK_NAME")
audio_path=$(echo "$clip_json" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('path',''))" 2>/dev/null)
clip_start_beat=$(echo "$clip_json" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('start_beat', 0.0))" 2>/dev/null)

if [ -n "$audio_path" ] && [ -f "$audio_path" ]; then
    echo "Found: $audio_path"
    echo "Arrangement start: beat $clip_start_beat"
else
    echo "Could not read from Ableton (has Ableton been restarted after the last AbletonOSC update?)."
    echo ""
    echo "Drag your Guide.wav into this window and press Enter:"
    read -r audio_path
    audio_path="${audio_path//\\ / }"
    audio_path="${audio_path#\'}" ; audio_path="${audio_path%\'}"
    audio_path="${audio_path#\"}" ; audio_path="${audio_path%\"}"
    clip_start_beat=0.0

    if [ ! -f "$audio_path" ]; then
        echo "File not found: $audio_path"
        read -n 1 -p "Press any key to close..."
        exit 1
    fi
fi

echo ""
echo "Optionally drag a Click Track WAV here for cleaner BPM detection,"
echo "or press Enter to use the Guide track:"
read -r click_path
click_path="${click_path//\\ / }"
click_path="${click_path#\'}" ; click_path="${click_path%\'}"
click_path="${click_path#\"}" ; click_path="${click_path%\"}"

if [ -n "$click_path" ] && [ ! -f "$click_path" ]; then
    echo "Click track not found — using Guide track for BPM."
    click_path=""
fi

echo ""
echo "Analyzing... (first run downloads Whisper model ~74MB)"
echo ""

python3 - "$audio_path" "$SCRIPT_DIR" "$TRACK_NAME" "$clip_start_beat" "$click_path" << 'PYEOF'
import sys, os

audio_path      = sys.argv[1]
script_dir      = sys.argv[2]
track_name      = sys.argv[3] if len(sys.argv) > 3 else "Guide"
clip_start_beat = float(sys.argv[4]) if len(sys.argv) > 4 and sys.argv[4] else 0.0
click_path      = sys.argv[5] if len(sys.argv) > 5 and sys.argv[5] else None
sys.path.insert(0, script_dir)

from bridge.analyzer import analyze_guide

result = analyze_guide(audio_path, click_path=click_path)

if not result.get("sections"):
    print("No sections detected. Make sure the Guide track has spoken cues.")
    sys.exit(1)

# Prepend a song header cue using the parent folder name
song_name = os.path.basename(os.path.dirname(audio_path)) or "Song"
result["sections"].insert(0, {"name": f"== {song_name} ==", "time": 0.0})

print(f"\nDetected BPM: {result['bpm']}")
print(f"Clip arrangement start: beat {clip_start_beat}")

sys.stdin = open('/dev/tty')  # reattach stdin after heredoc consumed it
bpm_input = input(f"Press Enter to accept BPM, or type the correct value: ").strip()
if bpm_input:
    try:
        result["bpm"] = float(bpm_input)
        print(f"Using BPM: {result['bpm']}")
    except ValueError:
        print("Invalid — using detected value.")

print(f"\nSections ({len(result['sections'])}):")
for s in result["sections"]:
    print(f"  {s['name']:<24} ({s['time']:.2f}s)")

print("\nSending to bridge...")
import json as _json
import urllib.request as _urlreq

payload = _json.dumps({
    "bpm": result["bpm"],
    "sections": result["sections"],
    "track_name": track_name,
    "clip_start_beat": clip_start_beat,
}).encode()

req = _urlreq.Request(
    "http://127.0.0.1:8766/apply_analysis",
    data=payload,
    headers={"Content-Type": "application/json"},
    method="POST",
)
try:
    with _urlreq.urlopen(req, timeout=10) as resp:
        body = _json.loads(resp.read())
        print(f"Done! {body.get('section_count')} markers sent to Ableton.")
        print("Check Ableton — markers should appear within a few seconds.")
except Exception as e:
    print(f"Could not reach bridge: {e}")
    print("Make sure the bridge is running (check Activity Monitor for python3).")
PYEOF

echo ""
read -n 1 -p "Press any key to close..."
