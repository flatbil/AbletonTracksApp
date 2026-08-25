"""
FastAPI + WebSocket server.

Connection roles: the first device to connect becomes "primary" (full
control). Every device after that connects as a read-only "observer" — it
receives the same state/position/tracks broadcasts but any control message it
sends (jump, mute_track, transport, generate_cues, analyze_guide,
release_control) is silently ignored. If primary disconnects, no observer is
auto-promoted — the slot stays empty until some device explicitly connects.

WebSocket protocol (all messages are JSON):

  Server → Client:
    { "type": "state",    "songs": [...], "position": float, "is_playing": bool,
      "current_song_index": int, "current_section_index": int,
      "role": "primary" | "observer", "connection_id": str }   ← on connect / marker change / refresh

    { "type": "position", "position": float, "is_playing": bool,
      "current_song_index": int, "current_section_index": int }   ← on every beat update

    { "type": "roster", "devices": [{"connection_id": str, "name": str,
      "role": "primary" | "observer"}, ...] }   ← whenever a device connects,
      disconnects, or registers a name — the "who's connected" list

  Client → Server:
    { "type": "jump",      "song_index": int, "section_index": int }   ← primary only
    { "type": "transport", "action": "play" | "stop" }                 ← primary only
    { "type": "refresh" }                                              ← primary or observer
    { "type": "register",  "name": str }                                ← primary or observer,
      declares this device's display name for the roster
"""

import asyncio
import json
import logging
import uuid

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from bridge.state import AppState

log = logging.getLogger(__name__)

app = FastAPI(title="AbletonAppPad Bridge")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# These are injected by main.py after startup
_state: AppState = None
_ableton = None


def init(state: AppState, ableton):
    global _state, _ableton
    _state = state
    _ableton = ableton


# ------------------------------------------------------------------ #
# Connection manager
# ------------------------------------------------------------------ #

class _ConnectionManager:
    def __init__(self):
        self._primary: WebSocket | None = None
        self._observers: set[WebSocket] = set()
        self._ids: dict[WebSocket, str] = {}
        self._names: dict[WebSocket, str] = {}

    async def connect(self, ws: WebSocket) -> tuple[str, str]:
        """Accept the socket and assign it a role + a stable connection id.
        The first connection becomes primary (full control); every connection
        after that becomes a read-only observer instead of being rejected."""
        await ws.accept()
        connection_id = uuid.uuid4().hex[:8]
        self._ids[ws] = connection_id
        self._names[ws] = "Unnamed Device"
        if self._primary is None:
            self._primary = ws
            log.info("Primary device connected (%s)", connection_id)
            return "primary", connection_id
        self._observers.add(ws)
        log.info("Observer device connected (%s, %d observing)", connection_id, len(self._observers))
        return "observer", connection_id

    def is_primary(self, ws: WebSocket) -> bool:
        return self._primary is ws

    def set_name(self, ws: WebSocket, name: str):
        self._names[ws] = name.strip() or "Unnamed Device"

    def roster(self) -> list[dict]:
        """Every connected device, primary first, for the "who's connected" list."""
        devices = []
        if self._primary is not None:
            devices.append({
                "connection_id": self._ids[self._primary],
                "name": self._names[self._primary],
                "role": "primary",
            })
        for ws in self._observers:
            devices.append({
                "connection_id": self._ids[ws],
                "name": self._names[ws],
                "role": "observer",
            })
        return devices

    def disconnect(self, ws: WebSocket):
        if self._primary is ws:
            self._primary = None
            log.info("Primary device disconnected")
        else:
            self._observers.discard(ws)
        self._ids.pop(ws, None)
        self._names.pop(ws, None)

    async def release(self):
        """Send control_released to primary and clear the slot. No observer is
        auto-promoted — whichever device next explicitly connects becomes
        primary, so control never silently transfers to a spectator."""
        ws = self._primary
        self._primary = None
        if ws:
            try:
                await ws.send_text(json.dumps({"type": "control_released"}))
                await ws.close(code=1000)
            except Exception:
                pass
        log.info("Primary released control")

    async def broadcast(self, message: dict):
        """Push to the primary and every observer."""
        text = json.dumps(message)
        targets = list(self._observers)
        if self._primary is not None:
            targets.append(self._primary)
        for ws in targets:
            try:
                await ws.send_text(text)
            except Exception:
                if self._primary is ws:
                    self._primary = None
                else:
                    self._observers.discard(ws)

    async def send(self, ws: WebSocket, message: dict):
        await ws.send_text(json.dumps(message))

    async def broadcast_roster(self):
        await self.broadcast({"type": "roster", "devices": self.roster()})


manager = _ConnectionManager()


# ------------------------------------------------------------------ #
# Callbacks (called by AbletonBridge, run in the asyncio event loop)
# ------------------------------------------------------------------ #

def on_state_change():
    asyncio.get_event_loop().create_task(
        manager.broadcast(_state.full_snapshot())
    )


def on_position_update():
    asyncio.get_event_loop().create_task(
        manager.broadcast(_state.position_snapshot())
    )


def on_tracks_change():
    asyncio.get_event_loop().create_task(
        manager.broadcast(_state.tracks_snapshot())
    )


# ------------------------------------------------------------------ #
# Routes
# ------------------------------------------------------------------ #

@app.get("/health")
def health():
    return {"status": "ok", "songs": len(_state.songs) if _state else 0}


@app.get("/guide_clip_path")
async def guide_clip_path(track: str = "Guide"):
    """Ask Ableton for the file path and arrangement position of the first clip on the named track."""
    loop = asyncio.get_running_loop()
    _ableton._pending_clip_path = loop.create_future()
    _ableton._client.send_message("/live/song/get/guide_clip_path", [track])
    try:
        path, start_beat = await asyncio.wait_for(_ableton._pending_clip_path, timeout=5.0)
    except asyncio.TimeoutError:
        return JSONResponse(
            {"error": f"timeout — Ableton did not respond. Make sure AbletonOSC is loaded and Ableton has been fully restarted since last updating AbletonOSC."},
            status_code=504,
        )
    finally:
        _ableton._pending_clip_path = None
    if not path:
        return JSONResponse({"error": f"no clip found on track '{track}'"}, status_code=404)
    return {"path": path, "start_beat": start_beat}


@app.post("/apply_analysis")
async def apply_analysis(request: Request):
    """
    Receives analysis results from the analyze_guide.command script and applies
    them to Ableton. Called from Terminal — no TCC restrictions.

    Body: {"bpm": float, "sections": [{"name": str, "beat": float}, ...]}
    """
    try:
        data = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)

    bpm = data.get("bpm")
    sections = data.get("sections", [])
    if not bpm or not sections:
        return JSONResponse({"error": "missing bpm or sections"}, status_code=400)

    track_name = data.get("track_name", "Cues")
    clip_start_beat = float(data.get("clip_start_beat", 0.0))
    log.info("apply_analysis: BPM=%.1f, %d sections, track='%s', clip_start_beat=%.2f",
             bpm, len(sections), track_name, clip_start_beat)

    async def _run():
        await _ableton.apply_analysis(bpm, sections, track_name, clip_start_beat)

    asyncio.get_event_loop().create_task(_run())
    return {"status": "ok", "bpm": bpm, "section_count": len(sections)}


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    role, connection_id = await manager.connect(ws)

    try:
        # Refresh from Ableton then send full state (with role + connection_id)
        # so the client gets current position, knows whether it has control,
        # and can pick itself out of the roster broadcast below.
        _ableton.refresh()
        snapshot = _state.full_snapshot()
        snapshot["role"] = role
        snapshot["connection_id"] = connection_id
        await manager.send(ws, snapshot)
        # A new device joined — let everyone (including this one) see the
        # updated "who's connected" list.
        await manager.broadcast_roster()

        async for raw in ws.iter_text():
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                log.warning("Bad JSON from client: %s", raw)
                continue

            msg_type = msg.get("type")

            # refresh and register are read-only/self-identifying — allowed
            # for observers too, not just the primary.
            if msg_type == "refresh":
                _ableton.refresh()
                snapshot = _state.full_snapshot()
                snapshot["role"] = role
                snapshot["connection_id"] = connection_id
                await manager.send(ws, snapshot)
                continue

            if msg_type == "register":
                manager.set_name(ws, str(msg.get("name", "")))
                await manager.broadcast_roster()
                continue

            if not manager.is_primary(ws):
                log.info("Ignoring '%s' from observer — not primary", msg_type)
                continue

            if msg_type == "jump":
                song_idx = msg.get("song_index", -1)
                sec_idx = msg.get("section_index", -1)
                _handle_jump(song_idx, sec_idx)

            elif msg_type == "mute_track":
                track_idx = msg.get("track_index", -1)
                muted = bool(msg.get("muted", False))
                if track_idx >= 0:
                    _ableton.set_track_mute(track_idx, muted)

            elif msg_type == "transport":
                action = msg.get("action")
                if action == "play":
                    _ableton.play()
                elif action == "stop":
                    _ableton.stop_playback()

            elif msg_type == "release_control":
                await manager.release()
                return

            elif msg_type == "generate_cues":
                track_name = msg.get("track_name", "Cues")
                log.info("Generating cues from track '%s'", track_name)
                asyncio.get_event_loop().create_task(
                    _ableton.generate_cues_from_track(track_name)
                )

            elif msg_type == "analyze_guide":
                track_name = msg.get("track_name", "Guide")
                model_size = msg.get("model_size", "base")
                log.info("Analyzing guide track '%s' with Whisper model '%s'", track_name, model_size)
                async def _run_analysis():
                    result = await _ableton.analyze_guide_track(track_name, model_size)
                    status = "done" if result.get("sections") else "error"
                    await manager.send(ws, {
                        "type": "analyze_guide_result",
                        "status": status,
                        "bpm": result.get("bpm"),
                        "section_count": len(result.get("sections", [])),
                    })
                asyncio.get_event_loop().create_task(_run_analysis())

            else:
                log.warning("Unknown message type: %s", msg_type)

    except WebSocketDisconnect:
        pass
    finally:
        manager.disconnect(ws)
        await manager.broadcast_roster()


def _handle_jump(song_idx: int, section_idx: int):
    try:
        section = _state.songs[song_idx]["sections"][section_idx]
        cue_index = int(section["cue_index"])
    except (IndexError, KeyError, TypeError) as e:
        log.error("Jump failed: %s", e)
        return

    # Send the jump directly — Ableton's own launch quantization handles bar-
    # boundary timing. A bridge-side sleep caused double-quantization (bridge
    # waits for bar, then Ableton also waits for bar), making jumps fire 2 bars
    # late and leaving the iOS progress bar frozen the whole time.
    log.info("Jumping to cue %d (song=%d section=%d)", cue_index, song_idx, section_idx)
    _ableton.jump_to_cue_index(cue_index)
