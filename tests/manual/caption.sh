#!/usr/bin/env bash
# Run the production FastVLM captioner on whatever is on screen right now and print
# the gist — the visual analogue of raw_outputs.sh. Mirrors real usage: ax_content
# --frame-only for app/window/window_id, capture the focused window, OCR for grounding,
# then caption (pad → vision → OCR-grounded prompt → decode).
#
# Usage: ./tests/manual/caption.sh [--no-ocr] [--tokens N] [--no-wait] [--model-dir DIR]
#   --no-ocr        caption from the image only (skip OCR grounding)
#   --tokens N      max new tokens (default 80)
#   --no-wait       skip the 4s countdown (capture immediately)
#   --model-dir DIR FastVLM model dir (default: data/models/fastvlm)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$SCRIPT_DIR/../.."
AX_SRC="$ROOT/patcha/macos/ax_content.swift"
AX_BIN="$ROOT/data/ax_content_raw"
OCR_BIN="$ROOT/data/ocr"
PATCHA_BIN="$ROOT/rust/target/release/patcha"
MODEL_DIR="$ROOT/data/models/fastvlm"
TMP_IMG="$(mktemp /tmp/patcha_cap_XXXXXX.png)"

NO_OCR=false
NO_WAIT=false
TOKENS=80
args=("$@")
for ((i=0; i<${#args[@]}; i++)); do
    case "${args[$i]}" in
        --no-ocr)    NO_OCR=true ;;
        --no-wait)   NO_WAIT=true ;;
        --tokens)    TOKENS="${args[$((i+1))]}" ;;
        --model-dir) MODEL_DIR="${args[$((i+1))]}" ;;
    esac
done

cleanup() { rm -f "$TMP_IMG"; }
trap cleanup EXIT

# --- build the patcha binary if needed ---
if [[ ! -x "$PATCHA_BIN" ]]; then
    echo "► Building patcha (release)..." >&2
    (cd "$ROOT/rust" && cargo build --release) >&2
fi
if [[ ! -f "$MODEL_DIR/onnx/decoder_model_merged_q4.onnx" ]]; then
    echo "► FastVLM model not found at $MODEL_DIR" >&2
    echo "►   pass --model-dir, or run build.sh to fetch it." >&2
    exit 1
fi

# --- compile ax_content helper (for app/window/window_id) ---
if [[ ! -f "$AX_BIN" || "$AX_SRC" -nt "$AX_BIN" ]]; then
    echo "► Compiling ax_content.swift..." >&2
    swiftc "$AX_SRC" -o "$AX_BIN"
fi

# --- countdown so you can switch to the app/screen you want to caption ---
if ! $NO_WAIT; then
    echo "► Capturing in 4s — switch to the app/screen you want to caption..." >&2
    for i in 4 3 2 1; do printf "\r  %ds " $i >&2; sleep 1; done
    printf "\r  capturing...   \n" >&2
fi

# --- app/window/window_id from ax_content (production path) ---
AX_JSON="$("$AX_BIN" --frame-only || echo '{}')"
APP="$(echo "$AX_JSON"    | python3 -c "import sys,json;print(json.load(sys.stdin).get('app',''))" 2>/dev/null || true)"
WINDOW="$(echo "$AX_JSON" | python3 -c "import sys,json;print(json.load(sys.stdin).get('window_title',''))" 2>/dev/null || true)"
WIN_ID="$(echo "$AX_JSON" | python3 -c "import sys,json;print(json.load(sys.stdin).get('window_id') or '')" 2>/dev/null || true)"

# --- capture the focused window (fall back to full screen) ---
CAP_CMD=(screencapture -x)
if [[ -n "$WIN_ID" ]]; then CAP_CMD+=(-o -l "$WIN_ID"); fi
"${CAP_CMD[@]}" "$TMP_IMG"
IMG_BYTES=$(wc -c < "$TMP_IMG" | tr -d ' ')
if [[ "$IMG_BYTES" -lt 10000 ]]; then
    echo "► WARNING: screenshot too small — grant Screen Recording in System Settings." >&2
    exit 1
fi

echo "" >&2
echo "=== context ===" >&2
echo "app='$APP'  window='$WINDOW'  (${IMG_BYTES} bytes)" >&2
echo "=== gist ===" >&2

# --- caption via the production captioner (single-image mode) ---
EVAL_ARGS=("$TMP_IMG" --model-dir "$MODEL_DIR" --ocr-bin "$OCR_BIN"
           --app "$APP" --window "$WINDOW" --max-new-tokens "$TOKENS")
$NO_OCR && EVAL_ARGS+=(--no-ocr)
RUST_LOG=warn "$PATCHA_BIN" caption-eval "${EVAL_ARGS[@]}"
