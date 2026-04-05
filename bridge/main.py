"""
Entry point. Starts the Ableton OSC bridge and the WebSocket server together.

Usage:
    python -m bridge.main

Ableton must be running with AbletonOSC installed as a remote script.
"""

import asyncio
import logging
import signal
import sys

import uvicorn

from bridge.ableton import AbletonBridge
from bridge.server import app, init, on_position_update, on_state_change
from bridge.state import AppState

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger(__name__)

HOST = "0.0.0.0"
PORT = 8766


async def run():
    state = AppState()
    ableton = AbletonBridge(
        state=state,
        on_position_update=on_position_update,
        on_state_change=on_state_change,
    )
    init(state, ableton)

    await ableton.start()
    log.info("Ableton bridge connected. Waiting for Ableton...")

    config = uvicorn.Config(app, host=HOST, port=PORT, log_level="warning")
    server = uvicorn.Server(config)

    log.info("WebSocket server starting on ws://%s:%d/ws", HOST, PORT)
    log.info("Connect your iPad to: ws://<this-machine-ip>:%d/ws", PORT)

    loop = asyncio.get_event_loop()

    def _shutdown(sig, frame):
        log.info("Shutting down...")
        ableton.stop()
        loop.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    await server.serve()


if __name__ == "__main__":
    asyncio.run(run())
