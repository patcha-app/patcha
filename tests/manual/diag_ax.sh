#!/usr/bin/env bash
# Build (if source is newer than binary) and run the AX content diagnostic.
# Usage: ./tests/manual/diag_ax.sh [--raw] [--content]
#   --raw      print raw JSON
#   --content  show full captured text after the summary

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$SCRIPT_DIR/../.."
SRC="$ROOT/patcha/macos/ax_content.swift"
BIN="$ROOT/data/ax_content_diag"

mkdir -p "$ROOT/data"

if [[ ! -f "$BIN" || "$SRC" -nt "$BIN" ]]; then
    echo "► Compiling ax_content.swift…" >&2
    swiftc "$SRC" -o "$BIN"
    echo "► Done." >&2
fi

RAW=false
SHOW_CONTENT=false
for arg in "$@"; do
    [[ "$arg" == "--raw"     ]] && RAW=true
    [[ "$arg" == "--content" ]] && SHOW_CONTENT=true
done

OUTPUT=$("$BIN" --diag)

if $RAW || ! command -v python3 &>/dev/null; then
    echo "$OUTPUT"
    exit 0
fi

# Pretty-print with highlights using python3
python3 - "$OUTPUT" "$SHOW_CONTENT" <<'PYEOF'
import sys, json

data = json.loads(sys.argv[1])
show_content = sys.argv[2].lower() == "true"

RED    = "\033[31m"
GREEN  = "\033[32m"
YELLOW = "\033[33m"
CYAN   = "\033[36m"
BOLD   = "\033[1m"
RESET  = "\033[0m"

trusted = data.get("ax_trusted", False)
trust_color = GREEN if trusted else RED
trust_label = "yes" if trusted else "NO  ← grant in System Settings → Privacy & Security → Accessibility"

print(f"\n{BOLD}{'─'*60}{RESET}")
print(f"{BOLD}AX trusted:{RESET} {trust_color}{trust_label}{RESET}")
print(f"{BOLD}App:{RESET}    {data['app']}")
print(f"{BOLD}Window:{RESET} {data['window_title']}")
print(f"{BOLD}Cursor:{RESET} {data['cursor_pos']}")
print(f"{'─'*60}{RESET}")

for s in data["strategies"]:
    icon  = f"{GREEN}✔{RESET}" if s["fired"] else f"{RED}✘{RESET}"
    print(f"\n{icon} {BOLD}Strategy {s['strategy']}: {s['name']}{RESET}")
    print(f"   detail  : {s['detail']}")
    if s.get("element_role"):
        print(f"   role    : {CYAN}{s['element_role']}{RESET}")
    if s.get("element_frame"):
        print(f"   frame   : {s['element_frame']}")
    if s.get("content_chars") is not None:
        print(f"   chars   : {s['content_chars']}")
    if s.get("content_preview"):
        print(f"   preview : {YELLOW}{s['content_preview']}{RESET}")

print(f"\n{'─'*60}")
cands = data.get("bfs_candidates", [])
if cands:
    print(f"{BOLD}BFS candidates ({len(cands)} found, sorted by area desc):{RESET}")
    for c in cands:
        sel = f" {GREEN}← selected{RESET}" if c["selected"] else ""
        print(f"  #{c['rank']} {CYAN}{c['role']}{RESET}  {c['frame']}{sel}")
        if c.get("content_preview"):
            print(f"      {YELLOW}{c['content_preview']}{RESET}")
else:
    print(f"{BOLD}BFS candidates:{RESET} none (0 AXWebArea/AXTextArea found)")

wa = data.get("web_area_analysis")
if wa:
    print(f"\n{'─'*60}")
    print(f"{BOLD}Web area analysis:{RESET}")
    print(f"   frame          : {wa['web_area_frame']}")
    lm_color = GREEN if wa["landmark_main_found"] else YELLOW
    lm_label = "yes" if wa["landmark_main_found"] else "no — using geometric fallback"
    print(f"   AXLandmarkMain : {lm_color}{lm_label}{RESET}")
    if wa.get("landmark_main_frame"):
        print(f"   main frame     : {wa['landmark_main_frame']}")
    print(f"   sidebar cutoff : x < {wa['sidebar_cutoff_x']:.0f}")
    if wa["all_landmarks"]:
        print(f"   all landmarks  :")
        for lm in wa["all_landmarks"]:
            print(f"      {CYAN}{lm}{RESET}")

winner_color = GREEN if data["winner"] != "ocr_needed" else RED
print(f"\n{BOLD}Winner:{RESET} {winner_color}{data['winner']}{RESET}\n")

if show_content:
    content = data.get("full_content")
    print(f"{'─'*60}")
    if content:
        print(f"{BOLD}Full captured text ({len(content)} chars):{RESET}\n")
        print(content)
    else:
        print(f"{RED}No content captured (ocr_needed){RESET}")
    print(f"{'─'*60}\n")
PYEOF
