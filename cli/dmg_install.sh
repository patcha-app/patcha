#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
INSTALL_DIR="/usr/local/bin"
HOME_DIR="$HOME"
LOG_DIR="$HOME/.memorai/logs"
PLIST="$HOME/Library/LaunchAgents/com.memorai.agent.plist"

echo "Installing memorai..."

# Copy binaries
if [ ! -w "$INSTALL_DIR" ]; then
    echo "Installing to $INSTALL_DIR (requires sudo)..."
    sudo cp "$SCRIPT_DIR/memorai" "$INSTALL_DIR/"
    sudo cp "$SCRIPT_DIR/memorai-mcp" "$INSTALL_DIR/"
else
    cp "$SCRIPT_DIR/memorai" "$INSTALL_DIR/"
    cp "$SCRIPT_DIR/memorai-mcp" "$INSTALL_DIR/"
fi

chmod +x "$INSTALL_DIR/memorai" "$INSTALL_DIR/memorai-mcp"

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
    <string>com.memorai.agent</string>

    <key>ProgramArguments</key>
    <array>
        <string>$INSTALL_DIR/memorai</string>
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
echo "memorai installed successfully."
echo "  CLI:    $INSTALL_DIR/memorai"
echo "  MCP:    $INSTALL_DIR/memorai-mcp"
echo "  Daemon: running via launchd (com.memorai.agent)"
echo ""
echo "Run 'memorai --help' to get started."
