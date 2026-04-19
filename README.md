# AbletonTracksApp — MD Buddy Bridge

A lightweight bridge that connects Ableton Live to the **MD Buddy** iPad app over WebSocket. It reads cue point markers from an Ableton set, organises them into a song/section tree, and streams live playback state to the iPad in real time — giving performers instant visual feedback and one-tap section jumping during a live show.

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
4. Open MD Buddy on your iPad — it connects automatically

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

## Troubleshooting

### Quick checklist — app not connecting

Work through these in order before anything else:

1. **Is the bridge running on the Mac?**
   Open Terminal and run:
   ```bash
   launchctl list | grep stagepad
   ```
   You should see a line with `com.nuthouse.stagepad-bridge`. If nothing appears, start it manually:
   ```bash
   cd /path/to/AbletonTracksApp
   bash start.sh
   ```

2. **Is AbletonOSC selected in Ableton?**
   Ableton → Preferences → Link/Tempo/MIDI → Control Surface → must show **AbletonOSC** in one of the slots. If it says "None", select it and restart Ableton.

3. **Are the iPad and Mac on the same network?**
   - iPad: Settings → Wi-Fi — note the network name
   - Mac: System Settings → Network — must match
   - On audio/dedicated networks with no internet: this is fine, the bridge does not need internet

4. **Test the bridge is reachable from the iPad:**
   Open Safari on the iPad and go to:
   ```
   http://[Mac IP address]:8766/health
   ```
   - ✅ Returns `{"status":"ok","songs":N}` → bridge is reachable, problem is in the app
   - ❌ Times out or "cannot connect" → network is blocking port 8766 (see below)

5. **Find the Mac's IP address:**
   On the Mac, open Terminal:
   ```bash
   ipconfig getifaddr en0
   ```
   If that returns nothing (e.g. on a wired connection), try:
   ```bash
   ipconfig getifaddr en1
   ```
   Or: System Settings → Network → click your active connection → IP Address.

6. **Enter the IP manually in the app:**
   Tap the gear icon (top right) → type the Mac's IP address → tap **Connect to Bridge**.

---

### Port 8766 is blocked (audio/venue networks)

Some venues use managed network switches or routers with **client isolation** — devices on the same Wi-Fi cannot talk to each other directly. This is common on dedicated audio networks.

**Signs:** Safari times out on `http://[Mac IP]:8766/health`, even though both devices are on the same network.

**Solutions (in order of ease):**

1. **Use a personal hotspot** — turn on iPhone hotspot, connect both the Mac and iPad to it. Completely bypasses venue network restrictions.

2. **Use a small travel router** (e.g. GL.iNet) — plug into a venue ethernet port or use in AP mode, connect both devices to it. Creates a clean local network.

3. **USB connection** — plug the iPad into the Mac via USB-C. The bridge advertises itself on the USB network interface automatically. No Wi-Fi needed.

4. **Ask the venue to open port 8766** — if you have access to the router/switch admin, add a rule allowing TCP traffic on port 8766 between devices.

---

### Bridge installed on the wrong Mac

The bridge must run on the **same Mac that is running Ableton Live**. If you have multiple Macs (e.g. a dedicated Ableton laptop and a separate Mac), install and run the bridge on the Ableton machine.

To install on a new Mac:
```bash
git clone https://github.com/flatbil/AbletonTracksApp.git
cd AbletonTracksApp
bash install.sh
```

Or download and run `MDBuddyBridge.pkg` from the releases page.

---

### Bridge installed but running old code

If the bridge was installed a while ago and you've updated the repo since, the running service may be using outdated code. Update and restart:

```bash
cd /path/to/AbletonTracksApp
git pull
bash install.sh   # re-runs the service installer with new code
```

Or just restart the service to pick up any changes:
```bash
launchctl unload ~/Library/LaunchAgents/com.nuthouse.stagepad-bridge.plist
launchctl load   ~/Library/LaunchAgents/com.nuthouse.stagepad-bridge.plist
```

---

### App shows "Connected" but no songs

- Check that your Ableton set has cue point markers using the `== Song Name ==` convention (see **Cue point naming convention** above)
- Make sure AbletonOSC is selected as a Control Surface in Ableton Preferences
- Try tapping the gear icon → **Connect to Bridge** to force a full refresh

---

### Ableton tempo not updating in the app

The bridge polls Ableton for tempo on every beat. If the displayed BPM is stuck:
1. Check the bridge log for errors: `tail -f ~/Library/Logs/StagePadBridge/bridge.log`
2. Restart the bridge (see above)
3. If the problem persists after a project reload in Ableton, close and reopen Ableton completely — AbletonOSC listeners are sometimes lost on project reload

---

### Songs not advancing in setlist order

MD Buddy respects the setlist order you set in the app (long-press a song pill to drag and reorder). When a song ends, it queues a jump to the next song in your setlist order on the next bar boundary.

- If the jump doesn't happen: check that the bridge is running and connected (green dot in app)
- If it jumps to the wrong song: reorder the setlist in the app and make sure the order is saved (the order is stored on the iPad between sessions)

---

### Checking bridge logs

Logs are written to:
```
~/Library/Logs/StagePadBridge/bridge.log
```

View live:
```bash
tail -f ~/Library/Logs/StagePadBridge/bridge.log
```

Key lines to look for:
| Log line | Meaning |
|----------|---------|
| `OSC listener started` | Bridge started successfully |
| `Bonjour service registered` | iPad can auto-discover this bridge |
| `Client connected. Total: 1` | iPad app connected |
| `Cue points changed — loaded N songs` | Ableton markers read successfully |
| `Invalid HTTP request received` | App connecting with wrong protocol — do a clean reinstall of the app |
| `WARNING: ... OSCServer` | AbletonOSC failed to load — close and reopen Ableton |

---

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
