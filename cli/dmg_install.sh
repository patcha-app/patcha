#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# When run from inside Patcha.app, binaries live alongside this script in Resources/

INSTALL_DIR="/usr/local/bin"
HOME_DIR="$HOME"
LOG_DIR="$HOME/.patcha/logs"
PLIST="$HOME/Library/LaunchAgents/com.patcha.agent.plist"

echo "Installing patcha..."

# Copy binary
if [ ! -w "$INSTALL_DIR" ]; then
    echo "Installing to $INSTALL_DIR (requires sudo)..."
    sudo cp "$SCRIPT_DIR/patcha" "$INSTALL_DIR/"
else
    cp "$SCRIPT_DIR/patcha" "$INSTALL_DIR/"
fi

chmod +x "$INSTALL_DIR/patcha"

# Create log directory
mkdir -p "$LOG_DIR"

# Install LaunchAgent plist
mkdir -p "$HOME/Library/LaunchAgents"

cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
    "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.patcha.agent</string>

    <key>ProgramArguments</key>
    <array>
        <string>$INSTALL_DIR/patcha</string>
        <string>start-daemon</string>
    </array>

    <key>RunAtLoad</key>
    <true/>

    <key>KeepAlive</key>
    <true/>

    <key>StandardOutPath</key>
    <string>$LOG_DIR/launchd.log</string>

    <key>StandardErrorPath</key>
    <string>$LOG_DIR/launchd.log</string>

    <key>EnvironmentVariables</key>
    <dict>
        <key>HOME</key>
        <string>$HOME_DIR</string>
    </dict>

    <key>ThrottleInterval</key>
    <integer>10</integer>
</dict>
</plist>
EOF

# Load the agent
launchctl unload "$PLIST" 2>/dev/null || true
launchctl load "$PLIST"

echo ""
echo "patcha installed successfully."
echo "  Binary: $INSTALL_DIR/patcha"
echo "  Daemon: running via launchd (com.patcha.agent)"
echo ""
echo "Run 'patcha --help' to get started."
