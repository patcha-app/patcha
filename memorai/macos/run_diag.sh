#!/usr/bin/env bash
# Compiles ax_content.swift (helpers only) + ax_content_diag.swift together,
# so the diag file never needs to duplicate the shared AX helper functions.
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
TMP=$(mktemp /tmp/ax_diag_XXXXXX.swift)
BIN=$(mktemp /tmp/ax_diag_bin_XXXXXX)
trap 'rm -f "$TMP" "$BIN"' EXIT

# Strip // MARK: - Main onward from ax_content.swift — keep only the helpers.
sed '/^\/\/ MARK: - Main$/,$d' "$DIR/ax_content.swift" > "$TMP"
# Append the diagnostic-only file.
cat "$DIR/ax_content_diag.swift" >> "$TMP"

swiftc "$TMP" -o "$BIN"
"$BIN"
