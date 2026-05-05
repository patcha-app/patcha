#!/usr/bin/env bash
set -e

cd "$(dirname "$0")"

VERSION=$(python3 -c "
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
DMG_STAGE="dist/dmg_stage/patcha"
rm -rf dist/dmg_stage
mkdir -p "$DMG_STAGE"

cp dist/bin/patcha "$DMG_STAGE/"
cp dist/bin/patcha-mcp "$DMG_STAGE/"
cp -r dist/Patcha.app "$DMG_STAGE/"
cp cli/dmg_install.sh "$DMG_STAGE/install.sh"
chmod +x "$DMG_STAGE/install.sh"

echo "  Contents staged at $DMG_STAGE"

# Create .dmg
echo ""
echo "Creating .dmg..."
DMG_PATH="dist/patcha-${VERSION}.dmg"
rm -f "$DMG_PATH"

hdiutil create \
    -volname "patcha" \
    -srcfolder dist/dmg_stage \
    -ov \
    -format UDZO \
    "$DMG_PATH"

echo ""
echo "Done: $DMG_PATH"
