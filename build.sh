#!/usr/bin/env bash
set -e

cd "$(dirname "$0")"

if ! command -v create-dmg &>/dev/null; then
    echo "Installing create-dmg..."
    brew install create-dmg
fi

VERSION=$(grep '^version' rust/patcha/Cargo.toml | head -1 | sed 's/version = "\(.*\)"/\1/')

SKIP_APP=false
for arg in "$@"; do
    [[ "$arg" == "--skip-app" ]] && SKIP_APP=true
done

# Signing configuration.
#   PATCHA_SIGN_IDENTITY  - "Developer ID Application: NAME (TEAMID)". When
#                           unset the build is signed ad-hoc for local testing
#                           and is NOT distributable.
#   PATCHA_NOTARY_PROFILE - notarytool keychain profile name. When unset,
#                           notarization is skipped.
SIGN_IDENTITY="${PATCHA_SIGN_IDENTITY:-}"
NOTARY_PROFILE="${PATCHA_NOTARY_PROFILE:-}"
APP_ENTITLEMENTS="swift-xcode/patcha/patcha/patcha.entitlements"
HELPER_ENTITLEMENTS="swift-xcode/patcha/patcha/helper.entitlements"

if [[ -z "$SIGN_IDENTITY" ]]; then
    ADHOC=true
    SIGN_IDENTITY="-"
    SIGN_FLAGS=()
else
    ADHOC=false
    # A secure timestamp is required for notarization and cannot be added after
    # the fact, so it must be part of every signature we produce.
    SIGN_FLAGS=(--timestamp)
    if ! security find-identity -v -p codesigning | grep -qF "$SIGN_IDENTITY"; then
        echo "Error: signing identity not found in keychain: $SIGN_IDENTITY"
        security find-identity -v -p codesigning | sed 's/^/  /'
        exit 1
    fi
fi

echo "Building patcha ${VERSION}..."

# Step 1: build native macOS menu bar app
echo ""
if $SKIP_APP; then
    echo "[1/7] Skipping Patcha.app build (--skip-app)."
    if [[ ! -d "dist/Patcha.app" ]]; then
        echo "Error: dist/Patcha.app not found. Run without --skip-app first."
        exit 1
    fi
else
    echo "[1/7] Building Patcha.app (Swift menu bar app)..."
    bash swift-xcode/patcha/build_app.sh
    echo "  Patcha.app built."
fi

# Step 2: compile Swift helper binaries (accessibility helpers)
echo ""
echo "[2/7] Compiling Swift helper binaries..."
mkdir -p data

if ! command -v swiftc &>/dev/null; then
    echo "Error: swiftc not found. Install Xcode or the Xcode Command Line Tools."
    exit 1
fi

swiftc helpers/ax_content.swift \
    -framework ApplicationServices \
    -framework AppKit \
    -framework Foundation \
    -o data/ax_content

swiftc helpers/ocr.swift \
    -framework Vision \
    -framework Foundation \
    -o data/ocr

swiftc helpers/mobileclip.swift \
    -framework CoreML \
    -framework Foundation \
    -O \
    -o data/mobileclip

swiftc helpers/observer.swift \
    -framework AppKit \
    -framework ApplicationServices \
    -framework Foundation \
    -O \
    -o data/observer

echo "  Swift helper binaries compiled."

# Step 3: fetch the MobileCLIP image-encoder Core ML model (visual pre-filter)
echo ""
echo "[3/7] Fetching MobileCLIP model..."
MLPKG="data/mobileclip_s2_image.mlpackage"
if [[ -f "$MLPKG/Data/com.apple.CoreML/weights/weight.bin" ]]; then
    echo "  Model already present, skipping download."
else
    BASE="https://huggingface.co/apple/coreml-mobileclip/resolve/main/mobileclip_s2_image.mlpackage"
    mkdir -p "$MLPKG/Data/com.apple.CoreML/weights"
    curl -fsSL "$BASE/Manifest.json" -o "$MLPKG/Manifest.json"
    curl -fsSL "$BASE/Data/com.apple.CoreML/model.mlmodel" -o "$MLPKG/Data/com.apple.CoreML/model.mlmodel"
    curl -fsSL "$BASE/Data/com.apple.CoreML/weights/weight.bin" -o "$MLPKG/Data/com.apple.CoreML/weights/weight.bin"
    echo "  Model downloaded to $MLPKG"
fi

# Step 4: Rust build (replaces PyInstaller)
echo ""
echo "[4/7] Building Rust binary..."
rm -rf dist/bin
mkdir -p dist/bin

(cd rust && cargo build --release)
cp rust/target/release/patcha dist/bin/patcha
chmod +x dist/bin/patcha

echo "  Rust binary built: dist/bin/patcha"

# Step 5: Assemble the .app payload
echo ""
echo "[5/7] Staging app payload..."
DMG_STAGE="dist/dmg_stage"
rm -rf "$DMG_STAGE"
mkdir -p "$DMG_STAGE"

cp -R dist/Patcha.app "$DMG_STAGE/"
STAGED_APP="$DMG_STAGE/Patcha.app"
APP_RES="$STAGED_APP/Contents/Resources"

cp dist/bin/patcha "$APP_RES/"

# Swift helper binaries + MobileCLIP model (resolved next to the patcha binary at runtime)
cp data/ax_content data/ocr data/mobileclip data/observer "$APP_RES/"
chmod +x "$APP_RES/patcha" "$APP_RES/ax_content" "$APP_RES/ocr" \
    "$APP_RES/mobileclip" "$APP_RES/observer"
cp -R data/mobileclip_s2_image.mlpackage "$APP_RES/"

# The FastVLM captioner model (~810 MB) is deliberately NOT bundled. The daemon
# fetches it into ~/.patcha/models/fastvlm on first run; see model_fetch.rs.
# Bundling it would quadruple the .dmg and add two multi-gigabyte notarization
# uploads per release.

# PatchaSourceRoot is a dev-only fallback pointing at this machine's checkout.
# Leaving it in would ship a local filesystem path in a public release.
/usr/libexec/PlistBuddy -c "Delete :PatchaSourceRoot" \
    "$STAGED_APP/Contents/Info.plist" 2>/dev/null || true

# Extended attributes left by cp/curl make codesign fail with
# "resource fork, Finder information, or similar detritus not allowed".
xattr -cr "$STAGED_APP"

echo "  Payload staged at $STAGED_APP"

# Step 6: Sign inside-out. Nested code must be signed before the enclosing
# bundle, otherwise sealing the app captures signatures that no longer match.
echo ""
if $ADHOC; then
    echo "[6/7] Signing ad-hoc (local testing only)..."
    echo "  WARNING: Gatekeeper will reject this bundle on any other machine."
    echo "  Set PATCHA_SIGN_IDENTITY to a Developer ID Application identity to"
    echo "  produce a distributable build."
else
    echo "[6/7] Signing with: $SIGN_IDENTITY"
fi

sign_code() {
    codesign --force --options runtime "${SIGN_FLAGS[@]}" \
        --sign "$SIGN_IDENTITY" --entitlements "$HELPER_ENTITLEMENTS" "$1"
}

for binary in patcha ax_content ocr mobileclip observer; do
    sign_code "$APP_RES/$binary"
done

# Nested resource bundles carry no entitlements of their own.
for nested in "$APP_RES"/*.bundle; do
    [[ -e "$nested" ]] || continue
    codesign --force --options runtime "${SIGN_FLAGS[@]}" \
        --sign "$SIGN_IDENTITY" "$nested"
done

codesign --force --options runtime "${SIGN_FLAGS[@]}" \
    --sign "$SIGN_IDENTITY" --entitlements "$APP_ENTITLEMENTS" "$STAGED_APP"

echo "  Verifying signature..."
codesign --verify --strict --deep --verbose=2 "$STAGED_APP"

if ! $ADHOC; then
    # Only a real Developer ID signature can satisfy Gatekeeper. Before
    # notarization this reports "rejected ... not notarized", which is expected.
    spctl -a -vvv -t exec "$STAGED_APP" 2>&1 | sed 's/^/    /' || true
fi

# Notarize and staple the app. Stapling before building the .dmg means the app
# still validates offline once the user drags it out of the disk image.
if [[ -n "$NOTARY_PROFILE" ]] && ! $ADHOC; then
    echo ""
    echo "  Notarizing app (this uploads the bundle to Apple and can take a while)..."
    APP_ZIP="dist/Patcha-notarize.zip"
    rm -f "$APP_ZIP"
    ditto -c -k --keepParent "$STAGED_APP" "$APP_ZIP"
    xcrun notarytool submit "$APP_ZIP" --keychain-profile "$NOTARY_PROFILE" --wait
    xcrun stapler staple "$STAGED_APP"
    rm -f "$APP_ZIP"
    echo "  App notarized and stapled."
elif ! $ADHOC; then
    echo ""
    echo "  Skipping notarization (PATCHA_NOTARY_PROFILE not set)."
fi

# Step 7: Create the .dmg
echo ""
echo "[7/7] Creating .dmg..."
DMG_PATH="dist/patcha-${VERSION}.dmg"
rm -f "$DMG_PATH"

create-dmg \
    --volname "Patcha" \
    --background "assets/dmg-background.png" \
    --window-size 660 400 \
    --icon-size 128 \
    --icon "Patcha.app" 180 185 \
    --app-drop-link 480 185 \
    --no-internet-enable \
    "$DMG_PATH" \
    "$DMG_STAGE/"

if ! $ADHOC; then
    echo "  Signing .dmg..."
    codesign --force "${SIGN_FLAGS[@]}" --sign "$SIGN_IDENTITY" "$DMG_PATH"
fi

if [[ -n "$NOTARY_PROFILE" ]] && ! $ADHOC; then
    echo "  Notarizing .dmg..."
    xcrun notarytool submit "$DMG_PATH" --keychain-profile "$NOTARY_PROFILE" --wait
    xcrun stapler staple "$DMG_PATH"
    echo "  Verifying stapled .dmg..."
    spctl -a -vvv -t install "$DMG_PATH" 2>&1 | sed 's/^/    /'
fi

echo ""
echo "Done: $DMG_PATH"
if $ADHOC; then
    echo ""
    echo "This is an ad-hoc build and is NOT distributable. To ship a release:"
    echo "  export PATCHA_SIGN_IDENTITY=\"Developer ID Application: NAME (TEAMID)\""
    echo "  export PATCHA_NOTARY_PROFILE=\"patcha-notary\""
    echo "See docs/RELEASING.md for the one-time setup."
fi
