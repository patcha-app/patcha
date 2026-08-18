# Collectors

`rust/patcha/src/collectors/`

Collectors pull raw activity data from sources on the user's machine and convert
them into `Event` objects (`rust/patcha/src/models.rs`). Each collector is
independent and is polled by the daemon on its own schedule. Toggle each one
with the `ENABLE_*_COLLECTOR` config variables.

Collectors that read fragile external state (browser databases, git repos) are
wrapped in a `CollectorGuard`, which disables a source after repeated failures
so one broken source can't stall the loop. Privacy filters in
`collectors/filters.rs` drop banking domains and incognito windows before
anything is stored.

---

## BrowserCollector (`browser.rs`)

Reads browser history SQLite databases directly from disk.

**Supported browsers:**
- Chrome — `~/Library/Application Support/Google/Chrome/Default/History`
- Arc — `~/Library/Application Support/Arc/User Data/Default/History` (Chrome-format schema)
- Safari — `~/Library/Safari/History.db` (different schema: `history_visits` + `history_items` join)

**How it works:**
- Copies the live database before opening (browsers hold a lock on it), reading via `rusqlite`
- Queries visits since the provided `since` timestamp
- Converts Chrome/Arc's microsecond Windows-epoch timestamps (offset `11_644_473_600_000_000`) to UTC
- Drops visits to banking domains (`filters::is_banking_domain`)

Each visit becomes an `Event(EventType::Browser)` whose `raw_content` carries the
title, url, and domain.

**Entry point:** `collect_all(since)` — runs all browsers and returns a merged,
timestamp-sorted list.

---

## TerminalCollector (`terminal.rs`)

Reads shell history files.

**Supported shells:**
- **zsh** (`~/.zsh_history`) — parses the extended `: timestamp:0;command` format, skipping malformed lines
- **bash** (`~/.bash_history`) — no timestamps; uses the file mtime and, for incremental runs, only the last handful of lines
- **fish** (`~/.local/share/fish/fish_history`) — YAML-like format, split on `- cmd:` with the `when:` timestamp

Each command becomes an `Event(EventType::Terminal)`.

**Entry point:** `collect_all(since)` — runs all three shells and returns a
merged, timestamp-sorted list.

---

## GitCollector (`git.rs`)

Captures git activity: commits, stashes, and staged-file changes, using
[`git2`](https://docs.rs/git2). It auto-discovers repositories by scanning the
home directory a few levels deep, skipping large non-code directories
(`Music`, `Pictures`, `Library`, `Applications`, iCloud, ...).

### Commits

Iterates commits since the provided timestamp (or the most recent ones on first
run). Each commit → `Event(EventType::GitCommit)` with the message and the list
of changed files.

### Stashes

Reads the stash list and each stash's stat summary → `Event(EventType::GitStash)`.

### Staging snapshots

Tracks when the staged file set changes (i.e. when you run `git add` / `git
reset`). The collector periodically records the staged/unstaged/untracked state
to a log under `DATA_DIR`, compares consecutive snapshots per repo, and emits an
`Event(EventType::GitStaged)` whenever the staged set changes. The log is trimmed
periodically (`STAGE_MAX_LOG_ROWS`) to stay bounded.

---

## WindowCollector (`window.rs`)

Tracks the active macOS app and window title.

- Runs a compiled Swift helper (`helpers/observer.swift`, resolved from the
  configured script path) to read the frontmost `app` and window `title`
- Appends captures to `window_log.jsonl` under `DATA_DIR`, ignoring focus blips
  shorter than `MIN_DURATION_SECS` (30s)
- Trims the log (`MAX_LOG_ROWS` = 100,000, every 1,000 writes)

The daemon reads this log to produce `Event(EventType::Window)` objects carrying
the app name and window title.

---

## AccessibilityCollector (`accessibility.rs`)

Captures on-screen content using macOS Accessibility APIs, with Vision-framework
OCR as a fallback, and enriches it with on-device perception.

Requires **Accessibility** and **Screen Recording** permission for the terminal
or `patcha` process.

**How it works:**
- Calls compiled Swift helpers (`helpers/ax_content.swift` for the AX tree,
  `helpers/ocr.swift` for Vision OCR) rather than dumping the whole screen
- Skips system apps and, importantly, **password managers** (1Password,
  Bitwarden, LastPass, Dashlane, KeePassXC, NordPass, Keychain Access)
- Skips incognito/private windows (`filters::is_incognito_window`) and content
  shorter than `MIN_CONTENT_LEN` (60 chars) or focus shorter than
  `MIN_DURATION_SECS` (4s)
- Runs a **visual prefilter** (MobileCLIP, tag `mobileclip_s2`): near-duplicate
  frames are dropped before any expensive work (see
  `config.visual_drop_threshold`)
- Captions the frame with an on-device **FastVLM** model (tag `fastvlm_0.5b`) to
  produce a short "gist" of what the user was doing, stored in the event
  metadata and prepended to the embedding text (see
  [embedding.md](embedding.md))
- Writes to `screen_log.jsonl` under `DATA_DIR`, trimmed periodically

The daemon reads `screen_log.jsonl` to produce `Event(EventType::Screen)`
objects. Perception (prefilter + captioner) is controlled by
`ENABLE_VISUAL_PREFILTER` and `ENABLE_CAPTIONER`.
