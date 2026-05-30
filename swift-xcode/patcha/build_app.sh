#!/usr/bin/env bash
set -e

cd "$(dirname "$0")"

if ! command -v xcodebuild &>/dev/null; then
    echo "Error: xcodebuild not found. Install Xcode."
    exit 1
fi

echo "Building Patcha.app (Xcode)..."
xcodebuild \
    -project patcha.xcodeproj \
    -scheme patcha \
    -configuration Release \
    -derivedDataPath .build \
    build 2>&1

APP_SRC=".build/Build/Products/Release/patcha.app"
APP_BUNDLE="../../dist/Patcha.app"

rm -rf "$APP_BUNDLE"
mkdir -p "../../dist"
cp -r "$APP_SRC" "$APP_BUNDLE"

echo "  Signing app bundle (ad-hoc)..."
codesign --force --deep --sign - "$APP_BUNDLE"

echo "  Built: $APP_BUNDLE"
