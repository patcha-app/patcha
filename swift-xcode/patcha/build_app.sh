#!/usr/bin/env bash
set -e

cd "$(dirname "$0")"

if ! command -v xcodebuild &>/dev/null; then
    echo "Error: xcodebuild not found. Install Xcode."
    exit 1
fi

echo "Building Patcha.app (Xcode)..."

# Built unsigned on purpose: build.sh copies the daemon, helper binaries and
# models into Contents/Resources afterwards, which would invalidate any
# signature applied here. All signing happens in build.sh after staging.
xcodebuild \
    -project patcha.xcodeproj \
    -scheme patcha \
    -configuration Release \
    -derivedDataPath .build \
    CODE_SIGNING_ALLOWED=NO \
    build 2>&1

APP_SRC=".build/Build/Products/Release/patcha.app"
APP_BUNDLE="../../dist/Patcha.app"

rm -rf "$APP_BUNDLE"
mkdir -p "../../dist"
cp -r "$APP_SRC" "$APP_BUNDLE"

echo "  Built (unsigned): $APP_BUNDLE"
