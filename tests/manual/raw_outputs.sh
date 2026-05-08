#!/usr/bin/env bash
# Show raw JSON output from ax_content and ocr binaries.
# OCR mirrors real usage: runs ax_content first, crops to its frame if source=ocr_needed.
#
# Usage: ./tests/manual/raw_outputs.sh [--ax-only] [--ocr-only] [--pretty]
#   --ax-only   show ax_content output only, skip OCR
#   --ocr-only  skip ax_content display, but still use it for frame crop
#   --pretty    pipe JSON output through python3 -m json.tool

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$SCRIPT_DIR/../.."
AX_SRC="$ROOT/patcha/macos/ax_content.swift"
OCR_SRC="$ROOT/patcha/macos/ocr.swift"
AX_BIN="$ROOT/data/ax_content_raw"
OCR_BIN="$ROOT/data/ocr_raw"
TMP_IMG="$(mktemp /tmp/patcha_ocr_XXXXXX.png)"

mkdir -p "$ROOT/data"

AX_ONLY=false
OCR_ONLY=false
PRETTY=false
for arg in "$@"; do
    [[ "$arg" == "--ax-only"  ]] && AX_ONLY=true
    [[ "$arg" == "--ocr-only" ]] && OCR_ONLY=true
    [[ "$arg" == "--pretty"   ]] && PRETTY=true
done

pretty() {
    if $PRETTY && command -v python3 &>/dev/null; then
        python3 -m json.tool
    else
        cat
    fi
}

cleanup() { rm -f "$TMP_IMG"; }
trap cleanup EXIT

# --- compile ax_content ---
if [[ ! -f "$AX_BIN" || "$AX_SRC" -nt "$AX_BIN" ]]; then
    echo "► Compiling ax_content.swift..." >&2
    swiftc "$AX_SRC" -o "$AX_BIN"
    echo "► Done." >&2
fi

# --- run ax_content, capture output ---
AX_JSON=$("$AX_BIN" || true)

if ! $OCR_ONLY; then
    echo "" >&2
    echo "=== ax_content raw output ===" >&2
    echo "$AX_JSON" | pretty
fi

if $AX_ONLY; then exit 0; fi

# --- compile ocr ---
if [[ ! -f "$OCR_BIN" || "$OCR_SRC" -nt "$OCR_BIN" ]]; then
    echo "► Compiling ocr.swift..." >&2
    swiftc "$OCR_SRC" -o "$OCR_BIN"
    echo "► Done." >&2
fi

echo "" >&2
echo "=== screencapture + ocr raw output ===" >&2

AX_SOURCE=$(echo "$AX_JSON" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('source',''))" 2>/dev/null || true)
echo "► ax source: ${AX_SOURCE:-unknown}" >&2

# Build screencapture command — crop to AX frame (+50px pad) when source=ocr_needed
CAP_CMD=(screencapture -x)
if [[ "$AX_SOURCE" == "ocr_needed" ]] && command -v python3 &>/dev/null; then
    CROP=$(echo "$AX_JSON" | python3 - <<'PYEOF'
import sys, json
try:
    d = json.load(sys.stdin)
    f = d.get("frame")
    if f and f.get("w", 0) > 0 and f.get("h", 0) > 0:
        pad = 50
        x = max(0, int(f["x"]) - pad)
        y = max(0, int(f["y"]) - pad)
        w = int(f["w"]) + pad * 2
        h = int(f["h"]) + pad * 2
        print(f"{x},{y},{w},{h}")
except (json.JSONDecodeError, TypeError):
    pass
PYEOF
    )
    if [[ -n "$CROP" ]]; then
        echo "► cropping to frame: $CROP (+ 50px pad)" >&2
        CAP_CMD+=(-R "$CROP")
    else
        echo "► no frame in AX output, full-screen capture" >&2
    fi
else
    echo "► AX extracted text directly — capturing full screen for comparison" >&2
fi

"${CAP_CMD[@]}" "$TMP_IMG"
IMG_BYTES=$(wc -c < "$TMP_IMG" | tr -d ' ')
echo "► screenshot size: ${IMG_BYTES} bytes" >&2
if [[ "$IMG_BYTES" -lt 10000 ]]; then
    echo "► WARNING: screenshot too small — Screen Recording permission may not be granted." >&2
    echo "►          Grant it in System Settings → Privacy & Security → Screen & System Audio Recording" >&2
    exit 1
fi

"$OCR_BIN" "$TMP_IMG" | pretty
