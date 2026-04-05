"""
Communicates with Ableton Live via AbletonOSC.

AbletonOSC defaults:
  Listens for commands : 127.0.0.1:11000
  Sends responses to  : 127.0.0.1:11001  (our listener)
"""

import asyncio
import logging
from typing import Callable

from pythonosc.dispatcher import Dispatcher
from pythonosc.osc_server import AsyncIOOSCUDPServer
from pythonosc.udp_client import SimpleUDPClient

from bridge.parser import find_current_indices, parse_markers
from bridge.state import AppState

log = logging.getLogger(__name__)

ABLETON_HOST = "127.0.0.1"
SEND_PORT = 11000   # AbletonOSC listens here
RECV_PORT = 11001   # we listen here for responses


class AbletonBridge:
    def __init__(self, state: AppState, on_position_update: Callable, on_state_change: Callable):
        self._state = state
        self._on_position_update = on_position_update  # called on every beat update
        self._on_state_change = on_state_change        # called when markers change

        self._client = SimpleUDPClient(ABLETON_HOST, SEND_PORT)
        self._dispatcher = Dispatcher()
        self._transport = None

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #

    async def start(self):
        self._dispatcher.map("/live/song/get/cue_points", self._handle_cue_points)
        self._dispatcher.map("/live/song/get/current_song_time", self._handle_position)
        self._dispatcher.map("/live/song/get/is_playing", self._handle_is_playing)
        self._dispatcher.set_default_handler(self._unhandled)

        server = AsyncIOOSCUDPServer(
            (ABLETON_HOST, RECV_PORT),
            self._dispatcher,
            asyncio.get_event_loop(),
        )
        self._transport, _ = await server.create_serve_endpoint()
        log.info("OSC listener started on %s:%d", ABLETON_HOST, RECV_PORT)

        # Subscribe to continuous beat-level position updates
        self._client.send_message("/live/song/start_listen/current_song_time", [])

        # Pull initial state
        self.refresh()

    def stop(self):
        if self._transport:
            self._client.send_message("/live/song/stop_listen/current_song_time", [])
            self._transport.close()

    def refresh(self):
        """Ask Ableton for a full state dump."""
        self._client.send_message("/live/song/get/cue_points", [])
        self._client.send_message("/live/song/get/is_playing", [])
        self._client.send_message("/live/song/get/current_song_time", [])

    # ------------------------------------------------------------------ #
    # Commands → Ableton
    # ------------------------------------------------------------------ #

    def jump_to_position(self, position: float):
        self._client.send_message("/live/song/set/current_song_time", position)

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

        self._state.songs = parse_markers(raw)
        log.info("Loaded %d songs from %d cue points", len(self._state.songs), len(raw))
        self._on_state_change()

    def _handle_position(self, address, *args):
        if not args:
            return
        self._state.current_position = float(args[0])
        s_idx, sc_idx = find_current_indices(self._state.songs, self._state.current_position)
        self._state.current_song_index = s_idx
        self._state.current_section_index = sc_idx
        self._on_position_update()

    def _handle_is_playing(self, address, *args):
        if not args:
            return
        self._state.is_playing = bool(args[0])
        self._on_position_update()

    def _unhandled(self, address, *args):
        log.debug("Unhandled OSC: %s %s", address, args)
