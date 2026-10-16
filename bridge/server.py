"""
FastAPI + WebSocket server.

Connection roles: exactly one device at a time is "primary" (full control).
Every other device is a read-only "observer" — it receives the same
state/position/tracks broadcasts but any control message it sends (jump,
mute_track, transport, generate_cues, analyze_guide, release_control,
respond_control, grant_control) is silently ignored.

A device becomes primary only by:
  - connecting while the slot is empty — except a handoff-capable device that
    was an observer before its socket dropped (prev_role=observer below): it
    rejoins as an observer, so control never lands on whoever happened to
    reconnect fastest.
  - reconnecting within RECLAIM_GRACE seconds of losing the slot. A handoff-
    capable primary's slot is held for its client_id meanwhile, so a one-
    second network blip on the MD's iPad never hands control to anyone else.
  - the primary accepting its control request, or granting control to it.
  - requesting control while the slot is empty, or while it's being held for a
    primary that then never comes back (granted when the grace window ends).
No observer is ever promoted silently.

Version compatibility: a client declares itself handoff-capable (protocol 2)
with query parameters on the socket URL, which older bridges simply ignore:
    ws://host:8766/ws?protocol=2&client_id=<stable per-install id>&prev_role=primary|observer
Every state message carries "protocol", so a client can tell whether this
bridge understands handoff (absent = an older bridge: show no handoff UI).
Legacy clients (no protocol param) keep the original rules exactly: they never
receive the protocol-2-only messages below, a legacy primary's slot empties
the instant it disconnects (it has no client_id to reclaim with), they're
never offered as a grant target, and a request aimed at a legacy primary is
refused as "unavailable" since it has no way to answer.

WebSocket protocol (all messages are JSON):

  Server → Client:
    { "type": "state",    "songs": [...], "position": float, "is_playing": bool,
      "current_song_index": int, "current_section_index": int,
      "role": "primary" | "observer", "connection_id": str, "protocol": int,
      "role_reason": str }   ← on connect / marker change / refresh. Also pushed
      to a device the moment its role changes, with role_reason set ("granted",
      "request_accepted", "handed_off", "reclaimed", "superseded",
      "slot_empty", "primary_timed_out") — a newly promoted device must
      hard-resync from this snapshot, never continue whatever it was
      dead-reckoning locally as an observer.

    { "type": "position", "position": float, "is_playing": bool,
      "current_song_index": int, "current_section_index": int }   ← on every beat update

    { "type": "roster", "devices": [{"connection_id": str, "name": str,
      "role": "primary" | "observer", "handoff": bool}, ...] }   ← whenever a
      device connects, disconnects, registers a name, or changes role — the
      "who's connected" list. handoff = that device speaks protocol 2 (only
      those can be granted control).

    { "type": "handoff", "pending": null | {"request_id": str,
      "connection_id": str, "name": str, "expires_in": float} }   ← protocol 2
      only; on connect and whenever the pending request changes. At most one
      request is pending at a time. expires_in is relative seconds, not a
      timestamp — the iPads' clocks aren't synced with the Mac's.

    { "type": "control_request_result", "request_id": str | null,
      "result": "denied" | "expired" | "cancelled" | "busy" | "unavailable",
      "reason": str }   ← protocol 2 only, to the requester, for every outcome
      except success (success is the role-change state push above).

    { "type": "jump_queued", "song_index": int, "section_index": int,
      "launch_beat": float }   ← sent to every device (primary and observers
      alike) the instant a jump is requested, before Ableton confirms it —
      launch_beat is the absolute beat position (1-bar-quantized) the jump
      will actually land on, so every screen can show the same count-in
      instead of an ambiguous "queued" flash with no sense of how long it'll be.

    { "type": "meters", "levels": {"<track_index>": float, ...} }   ← live
      per-track output level (0.0-1.0), on its own ~10Hz cadence. A track
      absent from this dict simply hasn't reported yet (nothing has played
      through it since it was subscribed) — treat that the same as 0.0.

  Client → Server:
    { "type": "jump",      "song_index": int, "section_index": int }   ← primary only
    { "type": "transport", "action": "play" | "stop" }                 ← primary only
    { "type": "refresh" }                                              ← primary or observer
    { "type": "register",  "name": str }                                ← primary or observer,
      declares this device's display name for the roster
    { "type": "request_control" }                                      ← observer, protocol 2
    { "type": "cancel_control_request" }                               ← the requesting observer
    { "type": "respond_control", "request_id": str, "accept": bool }   ← primary only
    { "type": "grant_control", "connection_id": str }                  ← primary only, proactive
      handoff to a protocol-2 observer — no confirmation step on the receiving end
"""

import asyncio
import json
import logging
import math
import uuid
from typing import Optional

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

HANDOFF_PROTOCOL = 2
# How long a control request waits for the primary to answer.
REQUEST_TIMEOUT = 20.0
# How long a protocol-2 primary's slot is held after its socket drops. Has to
# outlast the iPad's own reconnect loop: a 3s retry, then up to ~6s of Bonjour
# discovery and the saved-IP fallback before a socket is open again.
RECLAIM_GRACE = 15.0


class _Client:
    def __init__(self, connection_id: str, client_id: Optional[str], protocol: int):
        self.connection_id = connection_id
        # Stable per-install id — the only way to recognize the same iPad
        # across sockets. None for legacy clients, which never send one.
        self.client_id = client_id
        self.protocol = protocol
        self.name = "Unnamed Device"


class _Request:
    def __init__(self, request_id: str, requester: WebSocket, expires_at: float):
        self.request_id = request_id
        self.requester = requester
        self.expires_at = expires_at
        self.timer: Optional[asyncio.Task] = None


class _ConnectionManager:
    """Owns every role decision. Each public method mutates state fully before
    its first await, so concurrent handlers (and the two timers) always see a
    consistent primary/request/reservation — that's what makes two observers
    requesting in the same instant, or an accept racing an expiry, resolve
    cleanly: whichever the event loop processes first wins, the other sees the
    already-updated state."""

    def __init__(self):
        self._clients: dict[WebSocket, _Client] = {}
        self._primary: Optional[WebSocket] = None
        # client_id the empty primary slot is being held for, after a
        # protocol-2 primary's socket dropped.
        self._reserved_for: Optional[str] = None
        self._reserve_timer: Optional[asyncio.Task] = None
        self._request: Optional[_Request] = None

    # -- Queries ------------------------------------------------------------

    def is_primary(self, ws: WebSocket) -> bool:
        return self._primary is ws

    def role_of(self, ws: WebSocket) -> str:
        return "primary" if self._primary is ws else "observer"

    def connection_id(self, ws: WebSocket) -> str:
        return self._clients[ws].connection_id

    def supports_handoff(self, ws: WebSocket) -> bool:
        client = self._clients.get(ws)
        return client is not None and client.protocol >= HANDOFF_PROTOCOL

    def set_name(self, ws: WebSocket, name: str):
        self._clients[ws].name = name.strip() or "Unnamed Device"

    def roster(self) -> list[dict]:
        """Every connected device, primary first, for the "who's connected" list."""
        ordered = sorted(self._clients.items(), key=lambda item: item[0] is not self._primary)
        return [
            {
                "connection_id": client.connection_id,
                "name": client.name,
                "role": self.role_of(ws),
                "handoff": client.protocol >= HANDOFF_PROTOCOL,
            }
            for ws, client in ordered
        ]

    # -- Connect / disconnect -----------------------------------------------

    async def connect(self, ws: WebSocket) -> list:
        """Accept the socket and assign it a role + a stable connection id.
        Returns role changes this connect caused for *other* sockets, for
        announce()."""
        await ws.accept()
        params = ws.query_params
        try:
            protocol = int(params.get("protocol", "1"))
        except ValueError:
            protocol = 1
        client_id = (params.get("client_id") or None) if protocol >= HANDOFF_PROTOCOL else None
        client = _Client(uuid.uuid4().hex[:8], client_id, protocol)
        self._clients[ws] = client
        changes = self._assign_on_connect(ws, client, params.get("prev_role"))

        if self._primary is ws:
            log.info("Primary device connected (%s, protocol %d)", client.connection_id, protocol)
        else:
            log.info("Observer device connected (%s, protocol %d, %d observing)",
                     client.connection_id, protocol, len(self._clients) - (self._primary is not None))
        return changes

    def _assign_on_connect(self, ws: WebSocket, client: _Client, prev_role: Optional[str]) -> list:
        cid = client.client_id
        if cid and self._reserved_for == cid:
            self._end_reservation()
            self._primary = ws
            return []

        current = self._clients.get(self._primary) if self._primary is not None else None
        if cid and current is not None and current.client_id == cid:
            # This same install already holds primary on another socket: a dead
            # one the bridge hasn't noticed yet, or a sibling from the app
            # opening every Bonjour path in parallel. The newest socket takes
            # over; if the app keeps the older one instead, disconnect() hands
            # primary straight back to it.
            old = self._primary
            self._primary = ws
            return [(old, "superseded")]

        if self._primary is None and self._reserved_for is None:
            if not (cid and prev_role == "observer"):
                self._primary = ws
        return []

    def disconnect(self, ws: WebSocket) -> list:
        """Forget the socket. Returns role changes for announce()."""
        client = self._clients.pop(ws, None)
        if client is None:
            return []
        if self._request is not None and self._request.requester is ws:
            # Nobody left to tell the result to — announce()'s handoff
            # broadcast is what clears the primary's prompt.
            self._clear_request()
            log.info("Control request withdrawn — requester (%s) disconnected", client.connection_id)

        if self._primary is not ws:
            log.info("Observer device disconnected (%s)", client.connection_id)
            return []

        self._primary = None
        sibling = next(
            (w for w, c in self._clients.items() if client.client_id and c.client_id == client.client_id),
            None,
        )
        if sibling is not None:
            self._primary = sibling
            log.info("Primary socket closed — control stays with the same device (%s)",
                     self._clients[sibling].connection_id)
            return [(sibling, "reclaimed")]
        if client.client_id:
            self._start_reservation(client.client_id)
            log.info("Primary device disconnected (%s) — holding control for it for %.0fs",
                     client.connection_id, RECLAIM_GRACE)
        else:
            log.info("Primary device disconnected (%s)", client.connection_id)
        return []

    # -- Grace hold for a dropped primary -----------------------------------

    def _start_reservation(self, client_id: str):
        self._end_reservation()
        self._reserved_for = client_id
        self._reserve_timer = asyncio.get_running_loop().create_task(self._reservation_expiry(client_id))

    def _end_reservation(self):
        if self._reserve_timer is not None:
            self._reserve_timer.cancel()
        self._reserve_timer = None
        self._reserved_for = None

    async def _reservation_expiry(self, client_id: str):
        await asyncio.sleep(RECLAIM_GRACE)
        if self._reserved_for != client_id:
            return
        # Cleared by hand, not via _end_reservation() — that would cancel
        # this very task at its next await.
        self._reserve_timer = None
        self._reserved_for = None
        if self._primary is not None or self._request is None:
            log.info("Primary never came back — control slot is now empty")
            return
        # Someone explicitly asked for control and the device holding it is
        # gone for good: give it to them rather than leaving nobody in charge.
        req = self._clear_request()
        self._primary = req.requester
        log.info("Primary never came back — granting control to requester (%s)",
                 self._clients[req.requester].connection_id)
        await self.announce([(req.requester, "primary_timed_out")])

    # -- Handoff ------------------------------------------------------------

    async def request_control(self, ws: WebSocket):
        if self._primary is ws or not self.supports_handoff(ws):
            return
        req = self._request
        if req is not None:
            if req.requester is ws:
                await self._send_handoff(ws)   # duplicate tap — just resync its view
            else:
                await self._send_result(ws, None, "busy", "another_request_pending")
            return

        if self._primary is None and self._reserved_for is None:
            self._primary = ws
            log.info("Control requested with the slot empty — granted to %s",
                     self._clients[ws].connection_id)
            await self.announce([(ws, "slot_empty")])
            return

        if self._primary is not None and not self.supports_handoff(self._primary):
            await self._send_result(ws, None, "unavailable", "primary_outdated")
            return

        # Pending even while the slot is only reserved (primary mid-reconnect):
        # a returning primary sees the prompt, and if it never returns,
        # _reservation_expiry grants it.
        loop = asyncio.get_running_loop()
        req = _Request(uuid.uuid4().hex[:8], ws, loop.time() + REQUEST_TIMEOUT)
        req.timer = loop.create_task(self._request_expiry(req.request_id))
        self._request = req
        log.info("Control requested by %s", self._clients[ws].connection_id)
        await self.broadcast_handoff()

    async def _request_expiry(self, request_id: str):
        await asyncio.sleep(REQUEST_TIMEOUT)
        req = self._request
        if req is None or req.request_id != request_id:
            return
        req.timer = None   # don't let _clear_request cancel this running task
        self._clear_request()
        log.info("Control request %s expired unanswered", request_id)
        await self._send_result(req.requester, request_id, "expired", "no_response")
        await self.broadcast_handoff()

    def _clear_request(self) -> Optional[_Request]:
        req = self._request
        self._request = None
        if req is not None and req.timer is not None:
            req.timer.cancel()
        return req

    async def cancel_request(self, ws: WebSocket):
        req = self._request
        if req is None or req.requester is not ws:
            return
        self._clear_request()
        await self._send_result(ws, req.request_id, "cancelled", "withdrawn")
        await self.broadcast_handoff()

    async def respond(self, ws: WebSocket, request_id: str, accept: bool):
        if self._primary is not ws:
            return
        req = self._request
        if req is None or req.request_id != request_id:
            # Stale answer — the request already expired, was withdrawn, or its
            # device left between the prompt showing and the tap. Resend the
            # current state so the primary's prompt clears instead of hanging.
            await self._send_handoff(ws)
            return
        self._clear_request()
        if accept:
            await self.announce(self._transfer(req.requester, "request_accepted"))
        else:
            await self._send_result(req.requester, req.request_id, "denied", "declined")
            await self.broadcast_handoff()

    async def grant(self, ws: WebSocket, connection_id: str):
        if self._primary is not ws:
            return
        target = next((w for w, c in self._clients.items() if c.connection_id == connection_id), None)
        if target is None or target is ws or not self.supports_handoff(target):
            return
        req = self._clear_request()
        changes = self._transfer(target, "granted")
        if req is not None and req.requester is not target:
            await self._send_result(req.requester, req.request_id, "cancelled", "control_changed_hands")
        await self.announce(changes)

    def _transfer(self, to_ws: WebSocket, reason: str) -> list:
        old = self._primary
        self._primary = to_ws
        log.info("Control handed from %s to %s (%s)",
                 self._clients[old].connection_id if old in self._clients else "nobody",
                 self._clients[to_ws].connection_id, reason)
        changes = [(to_ws, reason)]
        if old is not None and old is not to_ws:
            changes.append((old, "handed_off"))
        return changes

    async def release(self):
        """Send control_released to primary and clear the slot. A pending
        requester gets control (they explicitly asked for it); otherwise nobody
        is promoted — whichever device next connects or requests takes it."""
        ws = self._primary
        self._primary = None
        changes = []
        req = self._clear_request()
        if req is not None:
            self._primary = req.requester
            changes.append((req.requester, "slot_empty"))
        if ws:
            try:
                await ws.send_text(json.dumps({"type": "control_released"}))
                await ws.close(code=1000)
            except Exception:
                pass
        log.info("Primary released control")
        await self.announce(changes)

    # -- Sending ------------------------------------------------------------

    async def announce(self, changes: list):
        """Push a fresh full snapshot (with its new role) to every device whose
        role just changed, then the current handoff state and roster to
        everyone."""
        for ws, reason in changes:
            if ws in self._clients:
                await self._safe_send(ws, _snapshot_for(ws, role_reason=reason))
        await self.broadcast_handoff()
        await self.broadcast_roster()

    def _handoff_message(self) -> dict:
        req = self._request
        if req is None or req.requester not in self._clients:
            return {"type": "handoff", "pending": None}
        remaining = req.expires_at - asyncio.get_running_loop().time()
        return {"type": "handoff", "pending": {
            "request_id": req.request_id,
            "connection_id": self._clients[req.requester].connection_id,
            "name": self._clients[req.requester].name,
            "expires_in": round(max(0.0, remaining), 2),
        }}

    async def _send_handoff(self, ws: WebSocket):
        if self.supports_handoff(ws):
            await self._safe_send(ws, self._handoff_message())

    async def broadcast_handoff(self):
        text = json.dumps(self._handoff_message())
        for ws in [w for w in self._clients if self.supports_handoff(w)]:
            await self._safe_send_text(ws, text)

    async def _send_result(self, ws: WebSocket, request_id: Optional[str], result: str, reason: str):
        if self.supports_handoff(ws):
            await self._safe_send(ws, {
                "type": "control_request_result",
                "request_id": request_id,
                "result": result,
                "reason": reason,
            })

    async def broadcast(self, message: dict):
        """Push to the primary and every observer. Primary goes first so a
        slow observer's socket can never delay the device actually in control.
        A failed send changes no roles here — the socket's own handler sees
        the disconnect and runs disconnect(), which is the one place role
        bookkeeping (grace hold included) happens."""
        text = json.dumps(message)
        targets = sorted(self._clients, key=lambda w: w is not self._primary)
        for ws in targets:
            await self._safe_send_text(ws, text)

    async def broadcast_full_state(self):
        """Like broadcast(), but for a full "state" snapshot specifically —
        personalizes role/connection_id/protocol per recipient the same way
        _snapshot_for does for the connect-time and role-change pushes. A
        single shared dict (the plain broadcast() above) would carry none of
        those fields, and the client defaults a missing "protocol" to 1 —
        silently resetting every client's handoff capability back to "no
        handoff" on every song/section change, the instant any regular state
        broadcast lands after the correct one from connect."""
        targets = sorted(self._clients, key=lambda w: w is not self._primary)
        for ws in targets:
            await self._safe_send(ws, _snapshot_for(ws))

    async def _safe_send(self, ws: WebSocket, message: dict):
        await self._safe_send_text(ws, json.dumps(message))

    async def _safe_send_text(self, ws: WebSocket, text: str):
        try:
            await ws.send_text(text)
        except Exception:
            pass

    async def send(self, ws: WebSocket, message: dict):
        await ws.send_text(json.dumps(message))

    async def broadcast_roster(self):
        await self.broadcast({"type": "roster", "devices": self.roster()})


manager = _ConnectionManager()


def _snapshot_for(ws: WebSocket, role_reason: Optional[str] = None) -> dict:
    """Full state as this particular device should see it — role is looked up
    live, never cached per socket, since a handoff can change it at any time."""
    snapshot = _state.full_snapshot()
    snapshot["role"] = manager.role_of(ws)
    snapshot["connection_id"] = manager.connection_id(ws)
    snapshot["protocol"] = HANDOFF_PROTOCOL
    if role_reason:
        snapshot["role_reason"] = role_reason
    return snapshot


# ------------------------------------------------------------------ #
# Callbacks (called by AbletonBridge, run in the asyncio event loop)
# ------------------------------------------------------------------ #

def on_state_change():
    asyncio.get_event_loop().create_task(
        manager.broadcast_full_state()
    )


def on_position_update():
    asyncio.get_event_loop().create_task(
        manager.broadcast(_state.position_snapshot())
    )


def on_tracks_change():
    asyncio.get_event_loop().create_task(
        manager.broadcast(_state.tracks_snapshot())
    )


def on_meter_update():
    asyncio.get_event_loop().create_task(
        manager.broadcast(_state.meters_snapshot())
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
    changes = await manager.connect(ws)

    try:
        # Refresh from Ableton then send full state (with role + connection_id)
        # so the client gets current position, knows whether it has control,
        # and can pick itself out of the roster broadcast below.
        _ableton.refresh()
        await manager.send(ws, _snapshot_for(ws))
        # A new device joined — let everyone (including this one) see the
        # updated "who's connected" list and any pending control request,
        # plus tell this device's own older socket if this one superseded it.
        await manager.announce(changes)

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
                await manager.send(ws, _snapshot_for(ws))
                continue

            if msg_type == "register":
                manager.set_name(ws, str(msg.get("name", "")))
                await manager.broadcast_roster()
                continue

            # Asking for (or withdrawing a request for) control is the one
            # thing an observer is allowed to do that isn't read-only.
            if msg_type == "request_control":
                await manager.request_control(ws)
                continue

            if msg_type == "cancel_control_request":
                await manager.cancel_request(ws)
                continue

            if not manager.is_primary(ws):
                log.info("Ignoring '%s' from observer — not primary", msg_type)
                continue

            if msg_type == "jump":
                song_idx = msg.get("song_index", -1)
                sec_idx = msg.get("section_index", -1)
                await _handle_jump(song_idx, sec_idx)

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
                    # Otherwise a jump queued right before the stop never
                    # reaches its landing check in ableton.py (that's the only
                    # other place these clear), and it sits in state forever —
                    # handed to every future resync as a "queued" jump with a
                    # launch beat that's now meaningless.
                    _state.queued_song_index = -1
                    _state.queued_section_index = -1
                    _state.queued_launch_beat = None

            elif msg_type == "release_control":
                await manager.release()
                return

            elif msg_type == "respond_control":
                await manager.respond(ws, str(msg.get("request_id", "")), bool(msg.get("accept", False)))

            elif msg_type == "grant_control":
                await manager.grant(ws, str(msg.get("connection_id", "")))

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
        await manager.announce(manager.disconnect(ws))


async def _handle_jump(song_idx: int, section_idx: int):
    try:
        section = _state.songs[song_idx]["sections"][section_idx]
        cue_index = int(section["cue_index"])
    except (IndexError, KeyError, TypeError) as e:
        log.error("Jump failed: %s", e)
        return

    # Ableton's launch quantization is 1 bar — compute the beat this jump will
    # actually land on and broadcast it to every device (primary AND observers)
    # before Ableton even confirms it, so everyone sees the same count-in
    # instead of an ambiguous "queued" flash with no sense of how long it'll
    # be. current_position is the bridge's own last-known beat, same ground
    # truth every client already anchors its own progress display to.
    beats_per_bar = _state.time_signature_numerator or 4
    launch_beat = (math.floor(_state.current_position / beats_per_bar) + 1) * beats_per_bar
    _state.queued_song_index = song_idx
    _state.queued_section_index = section_idx
    _state.queued_launch_beat = launch_beat
    await manager.broadcast({
        "type": "jump_queued",
        "song_index": song_idx,
        "section_index": section_idx,
        "launch_beat": launch_beat,
    })

    # Send the jump directly — Ableton's own launch quantization handles bar-
    # boundary timing. A bridge-side sleep caused double-quantization (bridge
    # waits for bar, then Ableton also waits for bar), making jumps fire 2 bars
    # late and leaving the iOS progress bar frozen the whole time.
    log.info("Jumping to cue %d (song=%d section=%d), landing at beat %.2f", cue_index, song_idx, section_idx, launch_beat)
    _ableton.jump_to_cue_index(cue_index)
