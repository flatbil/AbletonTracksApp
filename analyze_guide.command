#!/bin/bash
# Analyze Guide Track — double-click in Finder to run.
# Reads Guide.wav from Ableton, detects BPM from Click Track, creates markers.

echo "========================================="
echo "  StagePad — Guide Track Analyzer"
echo "========================================="
echo ""
echo "Drag your Guide.wav file into this window and press Enter:"
read -r audio_path
audio_path="${audio_path//\\ / }"
audio_path="${audio_path#\'}" ; audio_path="${audio_path%\'}"
audio_path="${audio_path#\"}" ; audio_path="${audio_path%\"}"

if [ ! -f "$audio_path" ]; then
    echo "File not found: $audio_path"
    read -n 1 -p "Press any key to close..."
    exit 1
fi

echo ""
echo "Drag your Click Track WAV into this window and press Enter"
echo "(or just press Enter to detect BPM from the Guide track instead):"
read -r click_path
click_path="${click_path//\\ / }"
click_path="${click_path#\'}" ; click_path="${click_path%\'}"
click_path="${click_path#\"}" ; click_path="${click_path%\"}"

if [ -n "$click_path" ] && [ ! -f "$click_path" ]; then
    echo "Click track not found — will use Guide track for BPM."
    click_path=""
fi

echo ""
echo "Analyzing..."
echo "(First run downloads Whisper model ~74MB — subsequent runs are faster)"
echo ""

TRACK_NAME="${1:-Cues}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
python3 - "$audio_path" "$SCRIPT_DIR" "$TRACK_NAME" "$click_path" << 'PYEOF'
import sys, os, time

audio_path = sys.argv[1]
script_dir = sys.argv[2]
track_name = sys.argv[3] if len(sys.argv) > 3 else "Cues"
click_path = sys.argv[4] if len(sys.argv) > 4 and sys.argv[4] else None
sys.path.insert(0, script_dir)

from bridge.analyzer import analyze_guide

result = analyze_guide(audio_path, click_path=click_path)

if not result.get("sections"):
    print("No sections detected. Make sure Guide.wav has spoken cues.")
    sys.exit(1)

# Prepend a song header cue at time 0 using the parent folder name
song_name = os.path.basename(os.path.dirname(audio_path)) or "Song"
result["sections"].insert(0, {"name": f"== {song_name} ==", "time": 0.0})

print(f"\nDetected BPM: {result['bpm']}")
sys.stdin = open('/dev/tty')  # reattach stdin to terminal (consumed by heredoc)
bpm_input = input(f"Press Enter to accept, or type the correct BPM: ").strip()
if bpm_input:
    try:
        result["bpm"] = float(bpm_input)
        print(f"Using BPM: {result['bpm']}")
    except ValueError:
        print("Invalid — using detected value.")

print(f"\nSections ({len(result['sections'])}):")
for s in result["sections"]:
    print(f"  {s['name']:<24} ({s['time']:.1f}s)")

print("\nSending to bridge...")
import json as _json
import urllib.request as _urlreq

result["track_name"] = track_name
payload = _json.dumps(result).encode()
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
