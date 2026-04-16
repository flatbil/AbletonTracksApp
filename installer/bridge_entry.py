"""PyInstaller entry point for the MD Buddy Bridge."""
import asyncio
import sys
import os

# When running as a PyInstaller bundle, _MEIPASS is the temp extraction dir.
# Add it to sys.path so the bridge package can be found.
if getattr(sys, "frozen", False):
    sys.path.insert(0, sys._MEIPASS)

from bridge.main import run
asyncio.run(run())
