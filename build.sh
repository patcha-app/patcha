#!/usr/bin/env bash
set -e

cd "$(dirname "$0")"

if ! command -v create-dmg &>/dev/null; then
    echo "Installing create-dmg..."
    brew install create-dmg
fi

VERSION=$(uv run python3 -c "
import tomllib
with open('pyproject.toml', 'rb') as f:
    print(tomllib.load(f)['project']['version'])
")

echo "Building patcha ${VERSION}..."

# Step 1: build native macOS menu bar app
echo ""
echo "[1/5] Building Patcha.app (Swift menu bar app)..."
bash swift/build_app.sh
echo "  Patcha.app built."

# Step 2: compile Swift helper binaries (accessibility helpers)
echo ""
echo "[2/5] Compiling Swift helper binaries..."
mkdir -p data

if ! command -v swiftc &>/dev/null; then
    echo "Error: swiftc not found. Install Xcode or the Xcode Command Line Tools."
    exit 1
fi

swiftc patcha/macos/ax_content.swift \
    -framework ApplicationServices \
    -framework AppKit \
    -framework Foundation \
    -o data/ax_content

swiftc patcha/macos/ocr.swift \
    -framework Vision \
    -framework Foundation \
    -o data/ocr

echo "  Swift helper binaries compiled."

# Step 4: PyInstaller builds
echo ""
echo "[4/5] Building Python executables..."
rm -rf dist/bin build

uv run pyinstaller patcha.spec \
    --noconfirm \
    --distpath dist/bin

uv run pyinstaller patcha_mcp.spec \
    --noconfirm \
    --distpath dist/bin

echo "  Python executables built."

# Step 5: Assemble .dmg staging area
echo ""
echo "[5/5] Staging .dmg contents..."
DMG_STAGE="dist/dmg_stage"
rm -rf "$DMG_STAGE"
mkdir -p "$DMG_STAGE"

cp -r dist/Patcha.app "$DMG_STAGE/"
cp dist/bin/patcha "$DMG_STAGE/Patcha.app/Contents/Resources/"
cp dist/bin/patcha-mcp "$DMG_STAGE/Patcha.app/Contents/Resources/"
chmod +x "$DMG_STAGE/Patcha.app/Contents/Resources/patcha" \
         "$DMG_STAGE/Patcha.app/Contents/Resources/patcha-mcp"

echo "  Contents staged at $DMG_STAGE"

# Create .dmg
echo ""
echo "Creating .dmg..."
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

echo ""
echo "Done: $DMG_PATH"
