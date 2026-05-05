#!/usr/bin/env bash
set -e

cd "$(dirname "$0")"

echo "Building PatchaApp (Swift)..."
swift build -c release 2>&1

APP_BUNDLE="../dist/Patcha.app"
CONTENTS="$APP_BUNDLE/Contents"
rm -rf "$APP_BUNDLE"
mkdir -p "$CONTENTS/MacOS" "$CONTENTS/Resources"

cp .build/release/PatchaApp "$CONTENTS/MacOS/PatchaApp"
cp Info.plist "$CONTENTS/Info.plist"
cp Resources/menubar-icon.svg "$CONTENTS/Resources/menubar-icon.svg"
cp Resources/AppIcon.icns "$CONTENTS/Resources/AppIcon.icns"

echo "  Built: $APP_BUNDLE"
