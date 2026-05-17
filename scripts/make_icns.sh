#!/bin/bash
# Usage: ./make_icns.sh icon.svg [output_name]
# Requires: librsvg (brew install librsvg)

set -e

SVG="${1:?Usage: $0 icon.svg [output_name]}"
OUTPUT="${2:-AppIcon}"
ICONSET="${OUTPUT}.iconset"

if ! command -v rsvg-convert &>/dev/null; then
    echo "rsvg-convert not found. Install with: brew install librsvg"
    exit 1
fi

mkdir -p "$ICONSET"

render() {
    local size=$1
    local filename=$2
    rsvg-convert -w "$size" -h "$size" "$SVG" -o "${ICONSET}/${filename}"
}

render 16    icon_16x16.png
render 32    icon_16x16@2x.png
render 32    icon_32x32.png
render 64    icon_32x32@2x.png
render 128   icon_128x128.png
render 256   icon_128x128@2x.png
render 256   icon_256x256.png
render 512   icon_256x256@2x.png
render 512   icon_512x512.png
render 1024  icon_512x512@2x.png

iconutil -c icns "$ICONSET" -o "${OUTPUT}.icns"

rm -rf "$ICONSET"

echo "Created ${OUTPUT}.icns"
