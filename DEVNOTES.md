# Developer Notes — AbletonTracksApp Bridge

## Architecture decisions

### Why OSC → WebSocket translation?

Ableton's only open real-time API is OSC via the AbletonOSC remote script. OSC works fine on localhost but isn't practical over a network to an iPad (UDP, no connection state, no authentication). WebSocket gives us a persistent, reliable connection with reconnection semantics that the iPad client can depend on.

### Why Bonjour service advertisement?

When an iPad is connected via USB, macOS exposes network interfaces over the USB link. Bonjour (mDNS) automatically resolves the service to the USB interface when available, falling back to WiFi otherwise. This means zero IP configuration — the iPad finds the bridge automatically.

The service type is `_stagepad._tcp.` on the local domain.

### Position units: beats, not seconds

All positions are in beats from the start of the Ableton set. This keeps everything tempo-agnostic at the protocol level and makes section boundary math exact (cue points are placed at beat positions in Ableton).

### Beat listener vs. `current_song_time` subscription

AbletonOSC's `/live/song/start_listen/beat` fires an OSC message only when the integer beat advances or on a backward seek. At 60 BPM this means a worst-case 1-second latency for section changes originating from Ableton.

To fix this, we also subscribe to `/live/song/start_listen/current_song_time`, which fires on every Live API tick (~60 Hz). In `_handle_beat`, sub-beat `current_song_time` updates are **silently dropped** unless the section index has changed. Beat-boundary updates are always forwarded for position anchoring. This gives:

- **Section changes**: detected and pushed within ~17ms regardless of beat position
- **Position anchoring**: still sent once per beat (not every 17ms) — the iPad interpolates between beats

### Cue index vs. beat position for jumping

We jump using `/live/song/cue_point/jump <index>` rather than seeking to a beat position. This is because:
1. AbletonOSC respects Ableton's **launch quantization** setting when jumping by cue index
2. Cue index is unambiguous — two cue points can share the same beat position but never the same index
3. It is consistent with what a musician expects (quantized launches, not hard cuts)

### `cue_index` vs. display order

`parse_markers` sorts cue points by position for display, but `cue_index` is preserved from Ableton's **original** unsorted list. AbletonOSC's `/live/song/cue_point/jump` takes the original list index. This distinction matters if cue points are out of chronological order in the set.

### Cue point poll interval

`CUE_POLL_INTERVAL = 2.0` seconds. The cue list only changes when the musician adds/removes/renames markers. A 2-second poll catches that without hammering Ableton. The beat subscription handles all real-time position updates.

### Section-change broadcast deduplication

`_last_section` tracks `(song_index, section_index)`. A `current_song_time` update that doesn't change these values is discarded without touching the WebSocket. This keeps WebSocket traffic at beat-level frequency during normal playback and only bursts on actual section transitions.

## Module map

| File | Role |
|------|------|
| `bridge/main.py` | Entry point — wires AbletonBridge + FastAPI server + Bonjour registration |
| `bridge/ableton.py` | OSC listener and translator — talks to AbletonOSC, calls callbacks |
| `bridge/server.py` | FastAPI app — WebSocket endpoint, connection manager, command dispatch |
| `bridge/state.py` | `AppState` dataclass — shared mutable state, snapshot methods |
| `bridge/parser.py` | `parse_markers` + `find_current_indices` — pure cue point logic |
| `AbletonOSC/` | Git submodule — Ableton remote script (not modified) |

## Known limitations

- Only one Ableton set can be active at a time. Switching sets clears the cue cache (detected via large backward position jump).
- The bridge assumes AbletonOSC is installed and selected as a control surface before the bridge starts. If not, the bridge runs but receives no OSC data until Ableton is configured.
- Launch quantization delay on jumps is intentional — Ableton holds the new position until the next quantization boundary. The iPad handles this via `pendingJumpPosition` suppression.
