#!/bin/bash
# MD Buddy Bridge — Installer Build Script
#
# Run this from the REPO ROOT:
#   bash installer/build.sh
#
# Outputs:
#   MDBuddyBridge.pkg          — installer (distribute this)
#   "Uninstall MD Buddy Bridge.command" — uninstaller (distribute alongside)
#
# Requirements (developer machine only):
#   pip3 install pyinstaller
#   Xcode Command Line Tools (for pkgbuild / productbuild)

set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

VERSION="1.0"
IDENTIFIER="com.nuthouse.stagepad-bridge"
PAYLOAD_ROOT="$SCRIPT_DIR/_payload"
COMPONENT_PKG="$SCRIPT_DIR/MDBuddyBridge_component.pkg"
OUTPUT_PKG="$REPO_ROOT/MDBuddyBridge.pkg"

echo "=== MD Buddy Bridge — Installer Builder ==="
echo ""

# ── 1. Build PyInstaller binary ───────────────────────────────────────────────
echo "[1/5] Building standalone binary with PyInstaller..."
pip3 install pyinstaller --quiet
pyinstaller installer/MDBuddyBridge.spec --noconfirm --clean
echo "      Done → dist/MDBuddyBridge/"

# ── 2. Stage payload ──────────────────────────────────────────────────────────
echo "[2/5] Staging installer payload..."
rm -rf "$PAYLOAD_ROOT"
INSTALL_DIR="$PAYLOAD_ROOT/Library/Application Support/MDBuddyBridge"
mkdir -p "$INSTALL_DIR"

# Bridge binary
cp -r dist/MDBuddyBridge "$INSTALL_DIR/"

# AbletonOSC remote script (bundled into the pkg so postinstall can deploy it)
cp -r AbletonOSC "$INSTALL_DIR/"

echo "      Payload staged at: $PAYLOAD_ROOT"

# ── 3. Build component package ────────────────────────────────────────────────
echo "[3/5] Building component package..."
chmod +x "$SCRIPT_DIR/scripts/preinstall"
chmod +x "$SCRIPT_DIR/scripts/postinstall"

pkgbuild \
    --root "$PAYLOAD_ROOT" \
    --scripts "$SCRIPT_DIR/scripts" \
    --identifier "$IDENTIFIER" \
    --version "$VERSION" \
    --install-location "/" \
    "$COMPONENT_PKG"

echo "      Component pkg: $COMPONENT_PKG"

# ── 4. Build distribution package ────────────────────────────────────────────
echo "[4/5] Building distribution package..."
productbuild \
    --distribution "$SCRIPT_DIR/distribution.xml" \
    --resources "$SCRIPT_DIR/resources" \
    --package-path "$SCRIPT_DIR" \
    "$OUTPUT_PKG"

echo "      Output: $OUTPUT_PKG"

# ── 5. Copy uninstaller alongside ────────────────────────────────────────────
echo "[5/5] Copying uninstaller..."
cp "$SCRIPT_DIR/Uninstall MD Buddy Bridge.command" "$REPO_ROOT/"
chmod +x "$REPO_ROOT/Uninstall MD Buddy Bridge.command"

# ── Cleanup ───────────────────────────────────────────────────────────────────
rm -f "$COMPONENT_PKG"
rm -rf "$PAYLOAD_ROOT"

echo ""
echo "=== Build complete ==="
echo ""
echo "Distribute these two files:"
echo "  → MDBuddyBridge.pkg"
echo "  → Uninstall MD Buddy Bridge.command"
echo ""
echo "To sign and notarize (required for Gatekeeper-free distribution):"
echo "  productsign --sign 'Developer ID Installer: YOUR NAME (TEAMID)' \\"
echo "              MDBuddyBridge.pkg MDBuddyBridge-signed.pkg"
echo "  xcrun notarytool submit MDBuddyBridge-signed.pkg \\"
echo "              --apple-id you@example.com --team-id TEAMID --wait"
echo "  xcrun stapler staple MDBuddyBridge-signed.pkg"
