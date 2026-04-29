# Collectors

`memorai/collectors/`

Collectors are responsible for pulling raw activity data from various sources on the user's machine and converting them into `Event` objects. Each collector is independent and can be polled on its own schedule.

---

## BrowserCollector (`browser.py`)

Reads browser history SQLite databases directly from disk.

**Supported browsers:**
- Chrome — `~/Library/Application Support/Google/Chrome/Default/History`
- Arc — same Chrome-format SQLite schema
- Safari — `~/Library/Safari/History.db` (different schema: `history_visits` + `history_items` join)

**How it works:**
- Copies the live database to a `.tmp` file before opening (browsers lock the file)
- Queries visits since the provided `since` timestamp
- Converts Chrome/Arc's microsecond epoch (offset from 1601-01-01) to UTC datetime
- Enhances YouTube URLs: if the title is empty or just a video ID, it falls back to `YouTube Video (ID: {id})` or prefixes `YouTube: ` to avoid useless entries

Each visit becomes an `Event(type=BROWSER)` with `raw_content` as a JSON-serialized `BrowserActivity` containing `title`, `url`, `domain`, `timestamp`.

**Entry point:** `collect_all(since)` — runs all three browsers and returns a merged, timestamp-sorted list.

---

## TerminalCollector (`terminal.py`)

Reads shell history files.

**Supported shells:**
- **zsh** (`~/.zsh_history`) — parses the extended format `: timestamp:0;command`, skips malformed lines
- **bash** (`~/.bash_history`) — no timestamps; uses file mtime as best estimate, limited to last 10 lines when `since` is provided
- **fish** (`~/.local/share/fish/fish_history`) — YAML-like format, splits on `- cmd:` then extracts `when:` timestamp

Each command becomes an `Event(type=TERMINAL)` with `raw_content` as a JSON-serialized `TerminalCommand`.

**Entry point:** `collect_all(since)` — runs all three shells and returns a merged, timestamp-sorted list.

---

## GitCollector (`git.py`)

Captures git activity: commits, stashes, and staged file changes.

### Commits (`collect_commits`)

- Auto-discovers git repos: checks current directory first, then scans up to 3 levels deep for `.git` directories
- Uses `gitpython` to iterate commits since the provided timestamp (or the latest 50 if no timestamp)
- Filters changed files to known language/config extensions to produce cleaner summaries; falls back to the first 5 files if none match
- Each commit → `Event(type=GIT_COMMIT)` with a serialized `GitCommit` payload

### Stashes (`collect_stashes`)

- Calls `git stash list` and `git stash show --stat` for each stash entry
- Each stash → `Event(type=GIT_STASH)` with a serialized `GitStash` payload

### Staging snapshots (`record_staging_snapshot` / `collect_staging_events`)

Tracks when the staged file set changes (i.e. when the user runs `git add` or `git reset`).

- `record_staging_snapshot()` — writes the current staged/unstaged/untracked state for all repos to `data/git_stage_snapshots.jsonl`
- `collect_staging_events(since)` — reads the snapshot log, compares consecutive snapshots per repo, and emits an `Event(type=GIT_STAGED)` whenever the staged set changes

This is called on a periodic interval by the daemon to detect active staging activity between commits.

---

## AccessibilityCollector (`accessibility.py`)

Captures on-screen text content using macOS Accessibility APIs and OCR as a fallback.

Requires macOS Accessibility permission for the terminal or memorai process (`System Settings > Privacy & Security > Accessibility`).

**How it works:**
- Compiles two Swift binaries on first run (if not already built): `ax_content.swift` (AX API) and `ocr.swift` (Vision framework OCR)
- Polls the active app and window on a configurable interval (`settings.poll_interval`)
- Skips system apps: Finder, System Preferences, Dock, etc.
- Detects the active frame (focused text field / mouse position) rather than dumping the entire screen
- When AX text extraction fails (`ocr_needed`), OCR is scoped to the same frame instead of capturing the full screen — `screencapture -R x,y,w,h` crops to the active element with 50 px padding
- Writes diffs to `data/screen_log.jsonl` — if the new content is >80% different from the last capture it stores the full text (new page), otherwise stores only the diff
- Trims the log file every 1,000 writes to stay under 100,000 lines

The daemon reads `screen_log.jsonl` to produce `Event(type=SCREEN)` objects.

---

## WindowCollector (`window.py`)

Tracks the active macOS app and window title via AppleScript.

- Runs `osascript window_title.applescript` with a 5-second timeout
- Script returns `app_name|||window_title` separated by `|||`
- Appends each capture to `data/window_log.jsonl`

The daemon reads this log to produce `Event(type=WINDOW)` objects with `metadata.app_name` and `metadata.window_title`.
