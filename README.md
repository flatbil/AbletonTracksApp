# AbletonTracksApp — StagePad Bridge

A Python bridge that connects Ableton Live to the StagePad iPad app over WebSocket. It reads cue point markers from an Ableton set, organises them into a song/section tree, and streams live playback state to the iPad in real time.

## How it works

```
Ableton Live  ←──OSC (localhost)──→  Bridge  ←──WebSocket (USB/WiFi)──→  StagePad (iPad)
```

- **AbletonOSC** (a Live remote script) exposes Ableton's transport and cue points via OSC on `localhost:11000/11001`
- The **bridge** (`bridge/`) translates that into a WebSocket server on port `8766`
- The bridge advertises itself via **Bonjour** (`_stagepad._tcp.`) so the iPad auto-discovers it over USB or WiFi — no manual IP entry needed

## Cue point naming convention

Structure your Ableton set with cue point markers following this pattern:

```
== Song Name ==       ← song header (double equals, any name)
Intro                 ← section
Verse 1
Chorus
Bridge
Outro

== Next Song ==
Verse 1
...
```

Song headers are matched by the regex `^==\s*(.+?)\s*==$`. Any cue point not matching a header is treated as a section of the current song.

## Setup

**Requirements:**
- macOS with Ableton Live 11+
- Python 3.10+

**One-time install:**
```bash
bash install.sh
```

This will:
1. Initialise the AbletonOSC git submodule
2. Install Python dependencies (`pip install -r requirements.txt`)
3. Copy AbletonOSC into Ableton's User Library Remote Scripts folder
4. Register the bridge as a macOS login service (auto-starts, auto-restarts on crash)

**In Ableton Live:**
- Preferences → Link/Tempo/MIDI → Control Surface → select **AbletonOSC**

**Manual start (without the login service):**
```bash
bash start.sh
```

**Logs (when running as a service):**
```bash
tail -f ~/Library/Logs/StagePadBridge/bridge.log
```

## WebSocket protocol

All messages are JSON. The server listens at `ws://<host>:8766/ws`.

### Server → Client

| Type | When sent | Fields |
|------|-----------|--------|
| `state` | On connect or cue point change | `songs`, `position`, `is_playing`, `current_song_index`, `current_section_index`, `tempo`, `time_signature_numerator` |
| `position` | Every beat, and immediately on section change | `position`, `is_playing`, `current_song_index`, `current_section_index`, `tempo`, `time_signature_numerator` |

`songs` structure:
```json
[
  {
    "name": "Amazing Grace",
    "position": 0.0,
    "sections": [
      { "name": "Start",   "position": 0.0,  "cue_index": 0 },
      { "name": "Verse 1", "position": 8.0,  "cue_index": 1 },
      { "name": "Chorus",  "position": 16.0, "cue_index": 2 }
    ]
  }
]
```

Positions are in **beats from the start of the Ableton set**.

### Client → Server

| Type | Fields | Effect |
|------|--------|--------|
| `jump` | `song_index`, `section_index` | Jumps to that section's cue point (respects Ableton launch quantization) |
| `transport` | `action`: `"play"` or `"stop"` | Starts or stops playback |
| `refresh` | — | Forces a full state resync |

## Dependencies

| Package | Purpose |
|---------|---------|
| `fastapi` | HTTP + WebSocket server |
| `uvicorn` | ASGI runner |
| `python-osc` | OSC encode/decode |
| `zeroconf` | Bonjour service advertisement |
| `websockets` | WebSocket runtime support for uvicorn |
