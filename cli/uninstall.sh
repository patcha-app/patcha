#!/usr/bin/env bash
set -euo pipefail

PLIST_DEST="$HOME/Library/LaunchAgents/com.memorai.agent.plist"
LABEL="com.memorai.agent"

echo "Uninstalling memorai background service..."

if [[ -f "$PLIST_DEST" ]]; then
    launchctl unload "$PLIST_DEST" 2>/dev/null || true
    rm "$PLIST_DEST"
    echo "Service stopped and plist removed."
else
    echo "No plist found at $PLIST_DEST — service may not be installed."
fi

echo "Done. Logs retained at: $HOME/Library/Logs/memorai/"
echo "To remove logs: rm -rf $HOME/Library/Logs/memorai/"
