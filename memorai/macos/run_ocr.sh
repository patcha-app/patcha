#!/usr/bin/env bash
# Compiles ocr.swift on first run (or when source is newer than the cached binary),
# then invokes it with the provided image path argument.
#   Usage: ./run_ocr.sh <image_path>
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
BIN="$DIR/.ocr_bin"

if [[ ! -f "$BIN" ]] || [[ "$DIR/ocr.swift" -nt "$BIN" ]]; then
    echo "Compiling ocr.swift..." >&2
    swiftc "$DIR/ocr.swift" -o "$BIN"
fi

"$BIN" "$@"
