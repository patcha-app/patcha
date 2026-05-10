#!/usr/bin/env bash
set -e

cd "$(dirname "$0")"

if ! command -v swift &>/dev/null; then
    echo "Error: swift not found. Install Xcode or the Xcode Command Line Tools."
    exit 1
fi

echo "Building PatchaApp (Swift)..."
swift build -c release 2>&1

APP_BUNDLE="../dist/Patcha.app"
CONTENTS="$APP_BUNDLE/Contents"
rm -rf "$APP_BUNDLE"
mkdir -p "$CONTENTS/MacOS" "$CONTENTS/Resources"

cp .build/release/PatchaApp "$CONTENTS/MacOS/PatchaApp"
cp Info.plist "$CONTENTS/Info.plist"
for icon in menubar-icon icon-recording icon-paused icon-error icon-starting; do
    [ -f "Resources/${icon}.svg" ] && cp "Resources/${icon}.svg" "$CONTENTS/Resources/${icon}.svg"
done

if [ -f Resources/AppIcon.icns ]; then
    cp Resources/AppIcon.icns "$CONTENTS/Resources/AppIcon.icns"
fi

echo "  Signing app bundle (ad-hoc)..."
codesign --force --deep --sign - "$APP_BUNDLE"

echo "  Built: $APP_BUNDLE"
