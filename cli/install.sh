#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
PLIST_TEMPLATE="$PROJECT_DIR/config/com.memorai.agent.plist"
LAUNCH_AGENTS_DIR="$HOME/Library/LaunchAgents"
PLIST_DEST="$LAUNCH_AGENTS_DIR/com.memorai.agent.plist"
LOG_DIR="$HOME/Library/Logs/memorai"
UV_BIN="$HOME/.local/bin/uv"
LABEL="com.memorai.agent"

echo "Installing memorai..."

if [[ ! -x "$UV_BIN" ]]; then
    echo "ERROR: uv not found at $UV_BIN"
    echo "Install uv: curl -LsSf https://astral.sh/uv/install.sh | sh"
    exit 1
fi

echo "Syncing dependencies..."
"$UV_BIN" sync --project "$PROJECT_DIR"

mkdir -p "$LOG_DIR"
mkdir -p "$LAUNCH_AGENTS_DIR"

sed \
    -e "s|MEMORAI_PROJECT_PATH|$PROJECT_DIR|g" \
    -e "s|MEMORAI_LOG_PATH|$LOG_DIR|g" \
    -e "s|MEMORAI_UV_BIN|$UV_BIN|g" \
    -e "s|MEMORAI_HOME|$HOME|g" \
    "$PLIST_TEMPLATE" > "$PLIST_DEST"

chmod 644 "$PLIST_DEST"

launchctl unload "$PLIST_DEST" 2>/dev/null || true
launchctl load "$PLIST_DEST"

echo "Service installed and started: $LABEL"
echo "Logs: $LOG_DIR/memorai.log"
echo "Status: launchctl list | grep memorai"
echo ""
echo "IMPORTANT: Grant Accessibility permission in"
echo "  System Settings -> Privacy & Security -> Accessibility"
