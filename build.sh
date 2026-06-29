#!/usr/bin/env bash
set -e

cd "$(dirname "$0")"

if ! command -v create-dmg &>/dev/null; then
    echo "Installing create-dmg..."
    brew install create-dmg
fi

VERSION=$(grep '^version' rust/patcha/Cargo.toml | head -1 | sed 's/version = "\(.*\)"/\1/')

SKIP_APP=false
for arg in "$@"; do
    [[ "$arg" == "--skip-app" ]] && SKIP_APP=true
done

echo "Building patcha ${VERSION}..."

# Step 1: build native macOS menu bar app
echo ""
if $SKIP_APP; then
    echo "[1/5] Skipping Patcha.app build (--skip-app)."
    if [[ ! -d "dist/Patcha.app" ]]; then
        echo "Error: dist/Patcha.app not found. Run without --skip-app first."
        exit 1
    fi
else
    echo "[1/5] Building Patcha.app (Swift menu bar app)..."
    bash swift-xcode/patcha/build_app.sh
    echo "  Patcha.app built."
fi

# Step 2: compile Swift helper binaries (accessibility helpers)
echo ""
echo "[2/5] Compiling Swift helper binaries..."
mkdir -p data

if ! command -v swiftc &>/dev/null; then
    echo "Error: swiftc not found. Install Xcode or the Xcode Command Line Tools."
    exit 1
fi

swiftc helpers/ax_content.swift \
    -framework ApplicationServices \
    -framework AppKit \
    -framework Foundation \
    -o data/ax_content

swiftc helpers/ocr.swift \
    -framework Vision \
    -framework Foundation \
    -o data/ocr

swiftc helpers/mobileclip.swift \
    -framework CoreML \
    -framework Foundation \
    -O \
    -o data/mobileclip

swiftc helpers/observer.swift \
    -framework AppKit \
    -framework ApplicationServices \
    -framework Foundation \
    -O \
    -o data/observer

echo "  Swift helper binaries compiled."

# Step 3: fetch the MobileCLIP image-encoder Core ML model (visual pre-filter)
echo ""
echo "[3/5] Fetching MobileCLIP model..."
MLPKG="data/mobileclip_s2_image.mlpackage"
if [[ -f "$MLPKG/Data/com.apple.CoreML/weights/weight.bin" ]]; then
    echo "  Model already present, skipping download."
else
    BASE="https://huggingface.co/apple/coreml-mobileclip/resolve/main/mobileclip_s2_image.mlpackage"
    mkdir -p "$MLPKG/Data/com.apple.CoreML/weights"
    curl -fsSL "$BASE/Manifest.json" -o "$MLPKG/Manifest.json"
    curl -fsSL "$BASE/Data/com.apple.CoreML/model.mlmodel" -o "$MLPKG/Data/com.apple.CoreML/model.mlmodel"
    curl -fsSL "$BASE/Data/com.apple.CoreML/weights/weight.bin" -o "$MLPKG/Data/com.apple.CoreML/weights/weight.bin"
    echo "  Model downloaded to $MLPKG"
fi

# Step 3b: fetch the FastVLM ONNX captioner model (gist captioning).
# CPU execution provider needs fp32-activation graphs: q4f16 vision/embed run on
# CPU, but the decoder must be the q4 (fp32) graph, not q4f16.
echo "  Fetching FastVLM captioner model..."
FVDIR="data/models/fastvlm"
if [[ -f "$FVDIR/onnx/decoder_model_merged_q4.onnx" ]]; then
    echo "  FastVLM model already present, skipping download."
else
    FVBASE="https://huggingface.co/onnx-community/FastVLM-0.5B-ONNX/resolve/main"
    mkdir -p "$FVDIR/onnx"
    for f in config.json tokenizer.json tokenizer_config.json special_tokens_map.json \
             generation_config.json preprocessor_config.json processor_config.json; do
        curl -fsSL "$FVBASE/$f" -o "$FVDIR/$f"
    done
    curl -fsSL "$FVBASE/onnx/vision_encoder_q4f16.onnx" -o "$FVDIR/onnx/vision_encoder_q4f16.onnx"
    curl -fsSL "$FVBASE/onnx/embed_tokens_q4f16.onnx" -o "$FVDIR/onnx/embed_tokens_q4f16.onnx"
    curl -fsSL "$FVBASE/onnx/decoder_model_merged_q4.onnx" -o "$FVDIR/onnx/decoder_model_merged_q4.onnx"
    echo "  FastVLM model downloaded to $FVDIR"
fi

# Step 4: Rust build (replaces PyInstaller)
echo ""
echo "[4/5] Building Rust binary..."
rm -rf dist/bin
mkdir -p dist/bin

(cd rust && cargo build --release)
cp rust/target/release/patcha dist/bin/patcha
chmod +x dist/bin/patcha

echo "  Rust binary built: dist/bin/patcha"

# Step 5: Assemble .dmg staging area
echo ""
echo "[5/5] Staging .dmg contents..."
DMG_STAGE="dist/dmg_stage"
rm -rf "$DMG_STAGE"
mkdir -p "$DMG_STAGE"

cp -r dist/Patcha.app "$DMG_STAGE/"
APP_RES="$DMG_STAGE/Patcha.app/Contents/Resources"
cp dist/bin/patcha "$APP_RES/"
chmod +x "$APP_RES/patcha"

# Swift helper binaries + MobileCLIP model (resolved next to the patcha binary at runtime)
cp data/ax_content data/ocr data/mobileclip data/observer "$APP_RES/"
chmod +x "$APP_RES/ax_content" "$APP_RES/ocr" "$APP_RES/mobileclip" "$APP_RES/observer"
cp -r data/mobileclip_s2_image.mlpackage "$APP_RES/"

# FastVLM captioner model (resolved at resources_dir/models/fastvlm at runtime).
# NOTE: ~0.8 GB — consider download-on-first-run instead of bundling to keep the DMG small.
mkdir -p "$APP_RES/models"
cp -r data/models/fastvlm "$APP_RES/models/"

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
