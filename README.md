# AbletonTracksApp — MD Buddy Bridge

A lightweight bridge that connects Ableton Live to the **StagePad** iPad app over WebSocket. It reads cue point markers from an Ableton set, organises them into a song/section tree, and streams live playback state to the iPad in real time — giving performers instant visual feedback and one-tap section jumping during a live show.

## How it works

```
Ableton Live  ←──OSC (localhost)──→  Bridge  ←──WebSocket (USB/WiFi)──→  StagePad (iPad)
```

- **AbletonOSC** (a Live remote script) exposes Ableton's transport and cue points via OSC on `localhost:11000/11001`
- The **bridge** (`bridge/`) translates that into a WebSocket server on port `8766`
- The bridge advertises itself via **Bonjour** (`_stagepad._tcp.`) so the iPad auto-discovers it over USB or WiFi — no manual IP entry needed
- When the iPad is connected via **USB-C**, the connection routes over USB for lower latency and no dependency on your venue's Wi-Fi

## System requirements

### Mac (running Ableton)
| Requirement | Minimum |
|-------------|---------|
| macOS | 12 Monterey or later |
| Ableton Live | 11 or later (Standard or Suite) |
| Architecture | Apple Silicon (M1+) or Intel — both supported |
| RAM | No additional requirement; the bridge uses ~30–50 MB |
| Disk | ~60 MB for the installer (PyInstaller bundle) |

### iPad (StagePad app)
| Requirement | Minimum |
|-------------|---------|
| iPadOS | 16 or later |
| Connection | Same Wi-Fi network as the Mac, or USB-C cable |

### Network
- The Mac and iPad must be on the **same local network** when using Wi-Fi, or connected via **USB-C** for a direct link
- Ports `8766` (TCP/WebSocket) and `11000/11001` (UDP/OSC, localhost only) must not be blocked by a firewall
- No internet connection required once installed

## Performance impact

The bridge is intentionally minimal. In normal use on a modern Mac:

| Metric | Typical value |
|--------|--------------|
| CPU usage | < 0.5% (idle between beats), < 1% during active playback |
| RAM | ~30–50 MB |
| Network (Wi-Fi) | < 1 KB/s to the iPad |
| Network (OSC, localhost) | ~60 messages/sec from Ableton's position listener |
| Disk I/O | None during playback |

The bridge uses asyncio throughout — it never blocks Ableton's audio thread. OSC messages from Ableton arrive via UDP and are dispatched on the event loop. WebSocket broadcasts are sent only when the section actually changes or on each beat tick, so the iPad connection is not flooded.

**Impact on Ableton:** The AbletonOSC remote script adds a lightweight Python listener inside Live. It has no measurable effect on audio buffer performance or CPU load in testing. It does not write to disk during playback.

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

## Installation

### Option A — Installer package (recommended, no technical setup required)

1. Download `StagePadBridge.pkg` and `Uninstall MD Buddy Bridge.command` from the releases page
2. Double-click `StagePadBridge.pkg` and follow the wizard
3. Open Ableton Live → **Preferences → Link/Tempo/MIDI → Control Surface** → select **AbletonOSC**
4. Open StagePad on your iPad — it connects automatically

> **Gatekeeper note:** If macOS blocks the installer, go to **System Settings → Privacy & Security** and click **Open Anyway**, or right-click the pkg and choose **Open**.

To uninstall: double-click `Uninstall MD Buddy Bridge.command`.

### Option B — From source (developers)

**Requirements:**
- macOS 12+, Ableton Live 11+
- Python 3.10+
- Git

```bash
git clone https://github.com/flatbil/AbletonTracksApp.git
cd AbletonTracksApp
bash install.sh
```

This will:
1. Install Python dependencies (`pip install -r requirements.txt`)
2. Copy AbletonOSC into Ableton's User Library Remote Scripts folder
3. Register the bridge as a macOS login service (auto-starts at login, auto-restarts on crash)

**In Ableton Live:**
- Preferences → Link/Tempo/MIDI → Control Surface → select **AbletonOSC**

**Manual start (without the login service):**
```bash
bash start.sh
```

**Build the installer package (developers):**
```bash
pip3 install pyinstaller
bash installer/build.sh
```
Outputs `StagePadBridge.pkg` + `Uninstall MD Buddy Bridge.command` in the repo root.

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
