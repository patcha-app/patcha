#!/usr/bin/env bash
set -e

cd "$(dirname "$0")"

VERSION=$(python3 -c "
import tomllib
with open('pyproject.toml', 'rb') as f:
    print(tomllib.load(f)['project']['version'])
")

echo "Building patcha ${VERSION}..."

# Step 1: compile Swift binaries
echo ""
echo "[1/4] Compiling Swift binaries..."
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

echo "  Swift binaries compiled."

# Step 2: PyInstaller builds
echo ""
echo "[2/4] Building Python executables..."
rm -rf dist/bin build

uv run pyinstaller patcha.spec \
    --noconfirm \
    --distpath dist/bin

uv run pyinstaller patcha_mcp.spec \
    --noconfirm \
    --distpath dist/bin

echo "  Python executables built."

# Step 3: Assemble .dmg staging area
echo ""
echo "[3/4] Staging .dmg contents..."
DMG_STAGE="dist/dmg_stage/patcha"
rm -rf dist/dmg_stage
mkdir -p "$DMG_STAGE"

cp dist/bin/patcha "$DMG_STAGE/"
cp dist/bin/patcha-mcp "$DMG_STAGE/"
cp cli/dmg_install.sh "$DMG_STAGE/install.sh"
chmod +x "$DMG_STAGE/install.sh"

echo "  Contents staged at $DMG_STAGE"

# Step 4: Create .dmg
echo ""
echo "[4/4] Creating .dmg..."
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
