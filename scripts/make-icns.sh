#!/usr/bin/env bash
set -e

usage() {
    echo "Usage: $0 <input.svg|input.png>"
    exit 1
}

[ $# -eq 1 ] || usage

INPUT="$1"
EXT="${INPUT##*.}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
OUT_DIR="$SCRIPT_DIR/../swift/Resources"
ICONSET="$(mktemp -d)/AppIcon.iconset"
mkdir -p "$ICONSET" "$OUT_DIR"

if [ "$EXT" = "svg" ]; then
    if ! command -v rsvg-convert &>/dev/null; then
        echo "rsvg-convert not found. Install with: brew install librsvg"
        exit 1
    fi
    BASE_PNG="$(mktemp /tmp/icon_1024.XXXXXX.png)"
    rsvg-convert -w 1024 -h 1024 "$INPUT" -o "$BASE_PNG"
elif [ "$EXT" = "png" ]; then
    BASE_PNG="$INPUT"
else
    echo "Unsupported format: .$EXT (use .svg or .png)"
    exit 1
fi

sips -z 16  16  "$BASE_PNG" --out "$ICONSET/icon_16x16.png"      > /dev/null
sips -z 32  32  "$BASE_PNG" --out "$ICONSET/icon_16x16@2x.png"   > /dev/null
sips -z 32  32  "$BASE_PNG" --out "$ICONSET/icon_32x32.png"      > /dev/null
sips -z 64  64  "$BASE_PNG" --out "$ICONSET/icon_32x32@2x.png"   > /dev/null
sips -z 128 128 "$BASE_PNG" --out "$ICONSET/icon_128x128.png"    > /dev/null
sips -z 256 256 "$BASE_PNG" --out "$ICONSET/icon_128x128@2x.png" > /dev/null
sips -z 256 256 "$BASE_PNG" --out "$ICONSET/icon_256x256.png"    > /dev/null
sips -z 512 512 "$BASE_PNG" --out "$ICONSET/icon_256x256@2x.png" > /dev/null
sips -z 512 512 "$BASE_PNG" --out "$ICONSET/icon_512x512.png"    > /dev/null
cp "$BASE_PNG"               "$ICONSET/icon_512x512@2x.png"

iconutil -c icns "$ICONSET" -o "$OUT_DIR/AppIcon.icns"

echo "Created: $OUT_DIR/AppIcon.icns"
