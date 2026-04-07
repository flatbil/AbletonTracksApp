"""
Communicates with Ableton Live via AbletonOSC.

AbletonOSC defaults:
  Listens for commands : 127.0.0.1:11000
  Sends responses to  : 127.0.0.1:11001  (our listener)
"""

import asyncio
import logging
import socket
from typing import Callable

from pythonosc.osc_message import OscMessage, ParseError
from pythonosc.osc_bundle import OscBundle
from pythonosc.udp_client import SimpleUDPClient

from bridge.parser import find_current_indices, parse_markers
from bridge.state import AppState

log = logging.getLogger(__name__)

ABLETON_HOST = "127.0.0.1"
SEND_PORT = 11000   # AbletonOSC listens here
RECV_PORT = 11001   # we listen here for responses (AbletonOSC default response port)
CUE_POLL_INTERVAL = 2.0  # seconds between cue point polls


class _OSCProtocol(asyncio.DatagramProtocol):
    """Minimal asyncio UDP protocol that dispatches OSC messages directly."""

    def __init__(self, bridge: "AbletonBridge"):
        self._bridge = bridge

    def datagram_received(self, data: bytes, addr):
        try:
            if OscBundle.dgram_is_bundle(data):
                bundle = OscBundle(data)
                for msg in bundle:
                    self._dispatch(msg)
            else:
                self._dispatch(OscMessage(data))
        except ParseError as e:
            log.warning("OSC parse error: %s", e)

    def _dispatch(self, msg: OscMessage):
        address = msg.address
        params = list(msg)
        if address == "/live/song/get/cue_points":
            self._bridge._handle_cue_points(address, *params)
        elif address in ("/live/song/get/beat", "/live/song/get/current_song_time"):
            self._bridge._handle_beat(address, *params)
        elif address == "/live/song/get/is_playing":
            self._bridge._handle_is_playing(address, *params)
        elif address == "/live/song/get/tempo":
            self._bridge._handle_tempo(address, *params)
        elif address == "/live/song/get/signature_numerator":
            self._bridge._handle_signature_numerator(address, *params)
        else:
            log.info("Unhandled OSC: %s %s", address, params)


class AbletonBridge:
    def __init__(self, state: AppState, on_position_update: Callable, on_state_change: Callable):
        self._state = state
        self._on_position_update = on_position_update  # called on every beat update
        self._on_state_change = on_state_change        # called when markers change

        self._client = SimpleUDPClient(ABLETON_HOST, SEND_PORT)
        self._transport = None
        self._poll_task = None
        self._last_raw_cues: list = []  # used to detect cue point changes
        self._last_section: tuple[int, int] = (-1, -1)  # for instant section-change detection

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #

    async def start(self):
        # Create socket manually with SO_REUSEADDR — prevents "port in use"
        # errors when restarting the bridge quickly.
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((ABLETON_HOST, RECV_PORT))

        loop = asyncio.get_event_loop()
        self._transport, _ = await loop.create_datagram_endpoint(
            lambda: _OSCProtocol(self),
            sock=sock,
        )
        log.info("OSC listener started on %s:%d", ABLETON_HOST, RECV_PORT)

        # Subscribe to beat-level position updates (fires every new beat and on seek)
        self._client.send_message("/live/song/start_listen/beat", [])
        # Subscribe to continuous position updates for instant section-change detection.
        # Fires on every Live API tick (~60 Hz) — we only broadcast from this when the
        # section index actually changes, so the WebSocket isn't flooded.
        self._client.send_message("/live/song/start_listen/current_song_time", [])
        # Subscribe to tempo, play state, and time signature changes
        self._client.send_message("/live/song/start_listen/tempo", [])
        self._client.send_message("/live/song/start_listen/is_playing", [])
        self._client.send_message("/live/song/start_listen/signature_numerator", [])

        # Pull initial state
        self.refresh()

        # Start background cue point poll
        self._poll_task = asyncio.get_event_loop().create_task(self._poll_cue_points())

    def stop(self):
        if self._poll_task:
            self._poll_task.cancel()
        if self._transport:
            self._client.send_message("/live/song/stop_listen/beat", [])
            self._client.send_message("/live/song/stop_listen/current_song_time", [])
            self._client.send_message("/live/song/stop_listen/signature_numerator", [])
            self._transport.close()

    async def _poll_cue_points(self):
        """Poll Ableton for cue point changes every CUE_POLL_INTERVAL seconds.
        Also refreshes position and play state so set switches are detected cleanly."""
        while True:
            await asyncio.sleep(CUE_POLL_INTERVAL)
            self._client.send_message("/live/song/get/cue_points", [])
            self._client.send_message("/live/song/get/current_song_time", [])
            self._client.send_message("/live/song/get/is_playing", [])

    def refresh(self):
        """Ask Ableton for a full state dump."""
        self._client.send_message("/live/song/get/cue_points", [])
        self._client.send_message("/live/song/get/is_playing", [])
        self._client.send_message("/live/song/get/current_song_time", [])
        self._client.send_message("/live/song/get/tempo", [])
        self._client.send_message("/live/song/get/signature_numerator", [])

    # ------------------------------------------------------------------ #
    # Commands → Ableton
    # ------------------------------------------------------------------ #

    def jump_to_cue_index(self, index: int):
        """Jump to a cue point by index — unambiguous and respects launch quantization."""
        self._client.send_message("/live/song/cue_point/jump", index)

    def play(self):
        self._client.send_message("/live/song/start_playing", [])

    def stop_playback(self):
        self._client.send_message("/live/song/stop_playing", [])

    # ------------------------------------------------------------------ #
    # OSC handlers ← Ableton
    # ------------------------------------------------------------------ #

    def _handle_cue_points(self, address, *args):
        """
        Args arrive as a flat interleaved list: name, time, name, time, ...
        Times are floats (beats from song start).
        Only broadcasts a state change if the cue points have actually changed.
        """
        raw = []
        args = list(args)
        i = 0
        while i + 1 < len(args):
            name = str(args[i])
            try:
                time = float(args[i + 1])
            except (ValueError, TypeError):
                i += 1
                continue
            raw.append({"name": name, "position": time})
            i += 2

        # Deduplicate — skip broadcast if nothing changed
        raw_key = [(c["name"], c["position"]) for c in raw]
        if raw_key == self._last_raw_cues:
            return
        self._last_raw_cues = raw_key

        self._state.songs = parse_markers(raw)
        s_idx, sc_idx = find_current_indices(self._state.songs, self._state.current_position)
        self._state.current_song_index = s_idx
        self._state.current_section_index = sc_idx
        log.info("Cue points changed — loaded %d songs from %d cue points", len(self._state.songs), len(raw))
        self._on_state_change()

    def _handle_beat(self, address, *args):
        if not args:
            return
        new_position = float(args[0])
        # A large backward jump likely means a new set was loaded — clear cue cache
        if new_position < self._state.current_position - 8:
            log.info("Position jumped back (%.2f → %.2f) — clearing cue cache for set reload", self._state.current_position, new_position)
            self._last_raw_cues = []
        self._state.current_position = new_position
        s_idx, sc_idx = find_current_indices(self._state.songs, self._state.current_position)
        self._state.current_song_index = s_idx
        self._state.current_section_index = sc_idx

        section_changed = (s_idx, sc_idx) != self._last_section
        is_beat_boundary = address == "/live/song/get/beat"

        if section_changed:
            # Section changed — broadcast immediately regardless of source
            log.info("Section changed to song=%d section=%d at position=%.2f", s_idx, sc_idx, new_position)
            self._last_section = (s_idx, sc_idx)
            self._on_position_update()
        elif is_beat_boundary:
            # No section change but this is a beat tick — send position update for
            # progress-bar anchoring on the iPad
            self._on_position_update()
        # else: sub-beat current_song_time update with no section change — drop it

    def _handle_is_playing(self, address, *args):
        if not args:
            return
        self._state.is_playing = bool(args[0])
        self._on_position_update()

    def _handle_tempo(self, address, *args):
        if not args:
            return
        self._state.tempo = float(args[0])
        self._on_position_update()

    def _handle_signature_numerator(self, address, *args):
        if not args:
            return
        self._state.time_signature_numerator = int(args[0])
        self._on_position_update()

