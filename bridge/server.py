"""
FastAPI + WebSocket server.

WebSocket protocol (all messages are JSON):

  Server → Client:
    { "type": "state",    "songs": [...], "position": float, "is_playing": bool,
      "current_song_index": int, "current_section_index": int }   ← on connect / marker change

    { "type": "position", "position": float, "is_playing": bool,
      "current_song_index": int, "current_section_index": int }   ← on every beat update

  Client → Server:
    { "type": "jump",      "song_index": int, "section_index": int }
    { "type": "transport", "action": "play" | "stop" }
    { "type": "refresh" }
"""

import asyncio
import json
import logging

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
        self._connections: list[WebSocket] = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self._connections.append(ws)
        log.info("Client connected. Total: %d", len(self._connections))

    def disconnect(self, ws: WebSocket):
        self._connections.remove(ws)
        log.info("Client disconnected. Total: %d", len(self._connections))

    async def broadcast(self, message: dict):
        if not self._connections:
            return
        data = json.dumps(message)
        dead = []
        for ws in self._connections:
            try:
                await ws.send_text(data)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self._connections.remove(ws)

    async def send(self, ws: WebSocket, message: dict):
        await ws.send_text(json.dumps(message))


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


# ------------------------------------------------------------------ #
# Routes
# ------------------------------------------------------------------ #

@app.get("/health")
def health():
    return {"status": "ok", "songs": len(_state.songs) if _state else 0}


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
    log.info("apply_analysis: BPM=%.1f, %d sections, track='%s'", bpm, len(sections), track_name)

    async def _run():
        clip_start = await _ableton.apply_analysis(bpm, sections, track_name)
        log.info("apply_analysis: clip was at beat %.2f in arrangement", clip_start)

    asyncio.get_event_loop().create_task(_run())
    return {"status": "ok", "bpm": bpm, "section_count": len(sections)}


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await manager.connect(ws)
    try:
        # Refresh from Ableton then send full state so the client gets current position
        _ableton.refresh()
        await manager.send(ws, _state.full_snapshot())

        async for raw in ws.iter_text():
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                log.warning("Bad JSON from client: %s", raw)
                continue

            msg_type = msg.get("type")

            if msg_type == "jump":
                song_idx = msg.get("song_index", -1)
                sec_idx = msg.get("section_index", -1)
                _handle_jump(song_idx, sec_idx)

            elif msg_type == "transport":
                action = msg.get("action")
                if action == "play":
                    _ableton.play()
                elif action == "stop":
                    _ableton.stop_playback()

            elif msg_type == "refresh":
                _ableton.refresh()
                await manager.send(ws, _state.full_snapshot())

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
                    # Send result back to this client so the app can show completion
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


def _handle_jump(song_idx: int, section_idx: int):
    try:
        section = _state.songs[song_idx]["sections"][section_idx]
        cue_index = int(section["cue_index"])
        log.info("Jumping to cue index %d (song=%d section=%d)", cue_index, song_idx, section_idx)
        _ableton.jump_to_cue_index(cue_index)
    except (IndexError, KeyError, TypeError) as e:
        log.error("Jump failed: %s", e)
