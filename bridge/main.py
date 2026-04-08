"""
Entry point. Starts the Ableton OSC bridge and the WebSocket server together.

Usage:
    python -m bridge.main

Ableton must be running with AbletonOSC installed as a remote script.
"""

import asyncio
import logging
import re
import signal
import socket
import subprocess
import sys

import uvicorn
from zeroconf import ServiceInfo
from zeroconf.asyncio import AsyncZeroconf

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


def _all_local_ipv4s() -> list[bytes]:
    """Return inet_aton-encoded bytes for every non-loopback IPv4 address.

    Uses ifconfig so USB/Thunderbolt interfaces (which don't appear in
    hostname resolution) are included. When the iPad is plugged in via USB,
    macOS assigns the Mac an IP on the USB network interface; advertising
    that IP lets iOS route the WebSocket connection over USB instead of WiFi.
    """
    seen: set[str] = set()
    addrs: list[bytes] = []

    # ifconfig gives us every active interface including USB NCM
    try:
        out = subprocess.run(["ifconfig"], capture_output=True, text=True, timeout=3).stdout
        for ip in re.findall(r"\binet (\d+\.\d+\.\d+\.\d+)\b", out):
            if not ip.startswith("127.") and ip not in seen:
                seen.add(ip)
                addrs.append(socket.inet_aton(ip))
    except Exception:
        pass

    # Fallback: hostname resolution
    if not addrs:
        try:
            ip = socket.gethostbyname(socket.gethostname())
            if not ip.startswith("127."):
                addrs.append(socket.inet_aton(ip))
        except Exception:
            pass

    return addrs or [socket.inet_aton("127.0.0.1")]


async def _register_bonjour(port: int) -> tuple[AsyncZeroconf, ServiceInfo]:
    """Advertise the bridge as _stagepad._tcp on all interfaces.

    Registering every local IPv4 (WiFi + USB) means iOS will resolve the
    service to whichever interface has the best path — USB when plugged in,
    WiFi otherwise — without any manual IP configuration.
    """
    addrs = _all_local_ipv4s()
    log.info("Bonjour: registering on %d address(es): %s", len(addrs),
             [socket.inet_ntoa(a) for a in addrs])

    info = ServiceInfo(
        "_stagepad._tcp.local.",
        "StagePad Bridge._stagepad._tcp.local.",
        addresses=addrs,
        port=port,
        properties={"version": "1"},
    )
    zc = AsyncZeroconf()
    await zc.async_register_service(info)
    log.info("Bonjour service registered — iPad will auto-discover this bridge")
    return zc, info


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

    zc, zc_info = await _register_bonjour(PORT)

    config = uvicorn.Config(app, host=HOST, port=PORT, log_level="warning")
    server = uvicorn.Server(config)

    log.info("WebSocket server starting on ws://%s:%d/ws", HOST, PORT)
    log.info("Connect your iPad to: ws://<this-machine-ip>:%d/ws", PORT)

    loop = asyncio.get_event_loop()

    def _shutdown(sig, frame):
        log.info("Shutting down...")
        ableton.stop()
        loop.create_task(zc.async_unregister_service(zc_info))
        loop.create_task(zc.async_close())
        server.should_exit = True

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    await server.serve()


if __name__ == "__main__":
    asyncio.run(run())
