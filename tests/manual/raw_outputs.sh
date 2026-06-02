#!/usr/bin/env bash
# Show raw JSON output from ax_content and ocr binaries.
# OCR mirrors real usage: runs ax_content first, crops to its frame if source=ocr_needed.
#
# Run the production OCR pipeline (ax_content --frame-only → cursor-centered crop → OCR →
# _reconstruct_layout) and print the reconstructed TEXT as it would be stored.
#
# Usage: ./tests/manual/raw_outputs.sh [--ax-only] [--raw-obs] [--pretty]
#   --ax-only   show ax_content --frame-only metadata only, skip OCR
#   --raw-obs   also dump the raw OCR coordinate observations (debugging)
#   --pretty    prettify any JSON shown (frame metadata, raw observations)

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
RAW_OBS=false
PRETTY=false
for arg in "$@"; do
    [[ "$arg" == "--ax-only" ]] && AX_ONLY=true
    [[ "$arg" == "--raw-obs" ]] && RAW_OBS=true
    [[ "$arg" == "--pretty"  ]] && PRETTY=true
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

# --- countdown so you can move cursor into the target app/column before capture ---
echo "► Capturing in 4s — switch to the app and position cursor in the column you want..." >&2
for i in 4 3 2 1; do printf "\r  %ds " $i >&2; sleep 1; done
printf "\r  capturing...    \n" >&2

# --- run ax_content in --frame-only mode (production path: metadata + cursor-centered crop) ---
AX_JSON=$("$AX_BIN" --frame-only || true)

echo "" >&2
echo "=== ax_content --frame-only output ===" >&2
echo "$AX_JSON" | pretty

if $AX_ONLY; then exit 0; fi

# --- compile ocr ---
if [[ ! -f "$OCR_BIN" || "$OCR_SRC" -nt "$OCR_BIN" ]]; then
    echo "► Compiling ocr.swift..." >&2
    swiftc "$OCR_SRC" -o "$OCR_BIN"
    echo "► Done." >&2
fi

echo "" >&2
echo "=== screencapture + ocr raw output ===" >&2

# Build screencapture command — crop to the cursor-centered frame (+50px pad).
CAP_CMD=(screencapture -x)
if command -v python3 &>/dev/null; then
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
        echo "► cropping to cursor-centered frame: $CROP (+ 50px pad)" >&2
        CAP_CMD+=(-R "$CROP")
    else
        echo "► no frame in AX output, full-screen capture" >&2
    fi
fi

"${CAP_CMD[@]}" "$TMP_IMG"
IMG_BYTES=$(wc -c < "$TMP_IMG" | tr -d ' ')
echo "► screenshot size: ${IMG_BYTES} bytes" >&2
if [[ "$IMG_BYTES" -lt 10000 ]]; then
    echo "► WARNING: screenshot too small — Screen Recording permission may not be granted." >&2
    echo "►          Grant it in System Settings → Privacy & Security → Screen & System Audio Recording" >&2
    exit 1
fi

OCR_JSON=$("$OCR_BIN" "$TMP_IMG")

# Raw coordinate observations only when explicitly requested (debugging).
if $RAW_OBS; then
    echo "" >&2
    echo "=== raw OCR observations ===" >&2
    echo "$OCR_JSON" | pretty
fi

# Headline output: the reconstructed TEXT, exactly as production stores it.
echo "" >&2
echo "=== reconstructed layout (_reconstruct_layout) ===" >&2
echo "$OCR_JSON" | "$ROOT/.venv/bin/python" -c "
import sys, json
from patcha.collectors.accessibility import AccessibilityCollector
obs = json.load(sys.stdin)
print(AccessibilityCollector._reconstruct_layout(obs))
"
