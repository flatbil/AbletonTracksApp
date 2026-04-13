# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for StagePad Bridge
# Run from the repo root: pyinstaller installer/StagePadBridge.spec

import os
block_cipher = None

a = Analysis(
    ['installer/bridge_entry.py'],
    pathex=['.'],          # repo root — makes 'bridge' package importable
    binaries=[],
    datas=[],
    hiddenimports=[
        # uvicorn dynamically imports these
        'uvicorn.logging',
        'uvicorn.loops',
        'uvicorn.loops.auto',
        'uvicorn.loops.asyncio',
        'uvicorn.loops.uvloop',
        'uvicorn.protocols',
        'uvicorn.protocols.http',
        'uvicorn.protocols.http.auto',
        'uvicorn.protocols.http.h11_impl',
        'uvicorn.protocols.http.httptools_impl',
        'uvicorn.protocols.websockets',
        'uvicorn.protocols.websockets.auto',
        'uvicorn.protocols.websockets.websockets_impl',
        'uvicorn.protocols.websockets.wsproto_impl',
        'uvicorn.lifespan',
        'uvicorn.lifespan.on',
        'uvicorn.lifespan.off',
        # websocket protocol
        'websockets',
        'websockets.legacy',
        'websockets.legacy.server',
        'websockets.legacy.protocol',
        'h11',
        'wsproto',
        # starlette / fastapi
        'starlette.routing',
        'starlette.middleware',
        'starlette.middleware.cors',
        'starlette.responses',
        'starlette.websockets',
        # zeroconf
        'zeroconf',
        'zeroconf.asyncio',
        'zeroconf._dns',
        'zeroconf._handlers',
        'zeroconf._handlers.answers',
        'zeroconf._handlers.record_manager',
        'zeroconf._handlers.query_handler',
        'zeroconf._utils',
        'zeroconf._utils.ipaddress',
        'zeroconf._utils.net',
        'zeroconf._utils.time',
        # python-osc
        'pythonosc',
        'pythonosc.osc_message',
        'pythonosc.osc_bundle',
        'pythonosc.udp_client',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Explicitly exclude the heavy optional deps (not used by bridge)
        'whisper', 'librosa', 'soundfile', 'torch', 'torchaudio',
        'numpy', 'scipy', 'matplotlib', 'PIL', 'cv2',
        'tkinter', 'test',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='StagePadBridge',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,   # UPX not needed on macOS, can cause signing issues
    console=True,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='StagePadBridge',
)
