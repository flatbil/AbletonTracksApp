#!/bin/bash
# MD Buddy Bridge — Installer Build Script
#
# Run this from the REPO ROOT:
#   bash installer/build.sh
#
# Outputs a signed, notarized, stapled installer ready to distribute with
# zero Gatekeeper warnings:
#   MDBuddyBridge.pkg                     — installer (distribute this)
#   "Uninstall MD Buddy Bridge.command"   — uninstaller (distribute alongside)
#
# Requirements (developer machine only):
#   pip3 install pyinstaller
#   Xcode Command Line Tools (for pkgbuild / productbuild)
#   Two certificates in your keychain (Xcode → Settings → Accounts →
#     Manage Certificates → "+"): "Developer ID Application" and
#     "Developer ID Installer"
#   Notarization credentials stored once via:
#     xcrun notarytool store-credentials "notarytool-profile" \
#       --apple-id you@example.com --team-id TEAMID
#   (run that command yourself — it prompts for an app-specific password
#   from appleid.apple.com and stores it in your keychain; this script
#   never sees or handles the password directly)

set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

# pip installs pyinstaller's CLI here on this machine's Python, which isn't
# on PATH by default.
export PATH="$HOME/Library/Python/3.9/bin:$PATH"

VERSION="1.1"
IDENTIFIER="com.nuthouse.stagepad-bridge"
PAYLOAD_ROOT="$SCRIPT_DIR/_payload"
COMPONENT_PKG="$SCRIPT_DIR/MDBuddyBridge_component.pkg"
OUTPUT_PKG="$REPO_ROOT/MDBuddyBridge.pkg"
SIGNED_PKG="$REPO_ROOT/MDBuddyBridge-signed.pkg"
APP_IDENTITY="Developer ID Application: William Almond (KNYS4NTTJS)"
INSTALLER_IDENTITY="Developer ID Installer: William Almond (KNYS4NTTJS)"
NOTARY_PROFILE="notarytool-profile"

echo "=== MD Buddy Bridge — Installer Builder ==="
echo ""

# ── 0. Preflight ───────────────────────────────────────────────────────────
if ! security find-identity -v -p basic | grep -q "Developer ID Application"; then
    echo "ERROR: No 'Developer ID Application' certificate found in keychain." >&2
    echo "       Xcode → Settings → Accounts → Manage Certificates → '+' → Developer ID Application" >&2
    exit 1
fi
if ! security find-identity -v -p basic | grep -q "Developer ID Installer"; then
    echo "ERROR: No 'Developer ID Installer' certificate found in keychain." >&2
    echo "       Xcode → Settings → Accounts → Manage Certificates → '+' → Developer ID Installer" >&2
    exit 1
fi
if ! xcrun notarytool history --keychain-profile "$NOTARY_PROFILE" >/dev/null 2>&1; then
    echo "ERROR: No notarytool credentials found under profile '$NOTARY_PROFILE'." >&2
    echo "       Run yourself (this script never handles the password directly):" >&2
    echo "       xcrun notarytool store-credentials \"$NOTARY_PROFILE\" --apple-id you@example.com --team-id KNYS4NTTJS" >&2
    exit 1
fi

# ── 1. Build PyInstaller binary ───────────────────────────────────────────────
echo "[1/8] Building standalone binary with PyInstaller..."
pip3 install pyinstaller --quiet
# Clean manually first — PyInstaller's own --clean uses Python's shutil.rmtree,
# which chokes on macOS "._*" AppleDouble sidecar files that exFAT volumes
# (e.g. an external drive) create alongside every file.
rm -rf build dist
pyinstaller installer/MDBuddyBridge.spec --noconfirm
echo "       Done → dist/MDBuddyBridge/"

# ── 2. Fix up the bundle before signing ──────────────────────────────────────
echo "[2/8] Cleaning bundle and fixing the Python3 framework symlinks..."
BUNDLE_DIR="dist/MDBuddyBridge"
find "$BUNDLE_DIR" -name "._*" -delete 2>&1 || true

# The bootloader dlopens "_internal/Python3" by that exact relative path, but
# it ships as a symlink into Python3.framework. pkgbuild's payload archiver
# dereferences symlinks into full duplicate copies — and for reasons never
# fully pinned down, the embedded code signature doesn't survive that
# dereference, so notarization flags these two paths as "signature invalid"
# even though the symlink target itself is validly signed. Fix: replace the
# *needed* one with a real signed copy (not a symlink) so pkgbuild can't
# mangle it; the framework-root convenience symlink isn't referenced by our
# runtime at all, so it's simply removed.
cd "$BUNDLE_DIR/_internal"
rm -f Python3 Python3.framework/Python3
cp -p Python3.framework/Versions/3.9/Python3 Python3
# Apple's original framework signature becomes stale once we re-sign below;
# leaving it in place causes a "code object is not signed at all" warning
# on the orphaned detached-signature directory.
rm -rf Python3.framework/Versions/3.9/.__CodeSignature
cd "$REPO_ROOT"

# ── 3. Sign every embedded binary (deepest first, main executable last) ──────
echo "[3/8] Signing all embedded binaries with Developer ID Application..."
cd "$BUNDLE_DIR"
find . -type f \( -name "*.so" -o -name "*.dylib" \) ! -name "._*" -print0 | \
    xargs -0 -I{} codesign --force --sign "$APP_IDENTITY" --options runtime --timestamp "{}"
codesign --force --sign "$APP_IDENTITY" --options runtime --timestamp \
    "_internal/Python3.framework/Versions/3.9/Python3"
codesign --force --sign "$APP_IDENTITY" --options runtime --timestamp \
    "_internal/Python3"
codesign --force --sign "$APP_IDENTITY" --options runtime --timestamp \
    "MDBuddyBridge"
codesign --verify --deep --strict "MDBuddyBridge"
echo "       All binaries signed and verified."
cd "$REPO_ROOT"

# ── 4. Stage payload ──────────────────────────────────────────────────────────
echo "[4/8] Staging installer payload..."
rm -rf "$PAYLOAD_ROOT"
INSTALL_DIR="$PAYLOAD_ROOT/Library/Application Support/MDBuddyBridge"
mkdir -p "$INSTALL_DIR"
cp -r "$BUNDLE_DIR" "$INSTALL_DIR/"
cp -r AbletonOSC "$INSTALL_DIR/"
find "$PAYLOAD_ROOT" -name "._*" -delete 2>&1 || true
echo "       Payload staged at: $PAYLOAD_ROOT"

# ── 5. Build component + distribution packages ────────────────────────────────
echo "[5/8] Building component package..."
chmod +x "$SCRIPT_DIR/scripts/preinstall" "$SCRIPT_DIR/scripts/postinstall"
pkgbuild \
    --root "$PAYLOAD_ROOT" \
    --scripts "$SCRIPT_DIR/scripts" \
    --identifier "$IDENTIFIER" \
    --version "$VERSION" \
    --install-location "/" \
    "$COMPONENT_PKG"

echo "[6/8] Building distribution package..."
productbuild \
    --distribution "$SCRIPT_DIR/distribution.xml" \
    --resources "$SCRIPT_DIR/resources" \
    --package-path "$SCRIPT_DIR" \
    "$OUTPUT_PKG"
rm -f "$COMPONENT_PKG"
rm -rf "$PAYLOAD_ROOT"
echo "       Output: $OUTPUT_PKG"

# ── 6. Sign, notarize, staple ─────────────────────────────────────────────────
echo "[7/8] Signing installer package..."
rm -f "$SIGNED_PKG"
productsign --sign "$INSTALLER_IDENTITY" "$OUTPUT_PKG" "$SIGNED_PKG"

echo "[8/8] Submitting for notarization (this can take several minutes)..."
xcrun notarytool submit "$SIGNED_PKG" --keychain-profile "$NOTARY_PROFILE" --wait
xcrun stapler staple "$SIGNED_PKG"
spctl --assess --type install -v "$SIGNED_PKG"
mv -f "$SIGNED_PKG" "$OUTPUT_PKG"

# ── 7. Copy uninstaller alongside ────────────────────────────────────────────
cp "$SCRIPT_DIR/Uninstall MD Buddy Bridge.command" "$REPO_ROOT/"
chmod +x "$REPO_ROOT/Uninstall MD Buddy Bridge.command"

echo ""
echo "=== Build complete — signed, notarized, and stapled ==="
echo ""
echo "Distribute these two files:"
echo "  → MDBuddyBridge.pkg"
echo "  → Uninstall MD Buddy Bridge.command"
