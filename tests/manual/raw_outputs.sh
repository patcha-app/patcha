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

# Build screencapture command — capture exactly the focused window by its CGWindowID.
CAP_CMD=(screencapture -x)
WIN_ID=$(echo "$AX_JSON" | python3 -c "import sys,json; print(json.load(sys.stdin).get('window_id') or '')" 2>/dev/null || true)
if [[ -n "$WIN_ID" ]]; then
    echo "► capturing focused window id=$WIN_ID (-o -l)" >&2
    CAP_CMD+=(-o -l "$WIN_ID")
else
    echo "► no window_id in AX output, full-screen capture" >&2
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

# Headline output: the reconstructed TEXT, exactly as production stores it — including the
# cursor/input-based pane selection (select_x derived from cursor_x/focus_x and the capture rect).
echo "" >&2
echo "=== reconstructed layout (_reconstruct_layout) ===" >&2
OCR_JSON="$OCR_JSON" AX_JSON="$AX_JSON" "$ROOT/.venv/bin/python" -c "
import os, json
from patcha.collectors.accessibility import AccessibilityCollector as AC
obs = json.loads(os.environ['OCR_JSON'])
ax = json.loads(os.environ['AX_JSON'] or '{}')
# Window-id capture: image bounds == window frame, so normalize cursor by the frame.
f = ax.get('frame') or {}
cap_x = int(f['x']) if f else None
cap_w = int(f['w']) if f else None
select_x = AC._select_x_in_capture(ax.get('cursor_x'), ax.get('focus_x'), cap_x, cap_w)
print(f'(select_x={select_x}  cap_x={cap_x} cap_w={cap_w})')
print(AC._reconstruct_layout(obs, select_x=select_x))
"
